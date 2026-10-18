"""Machine-readable definition and neutral example for Campaign Plan format version 2."""

from __future__ import annotations

_IDENTITY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
_PORTABLE_PATH_PATTERN = (
    r"^(?![A-Za-z]:)(?!/)(?!.*\\)(?!.*//)"
    r"(?!.*(?:^|/)\.{1,2}(?:/|$))(?!.*(?:^|/)\s)(?!.*\s(?:/|$))[^\x00]+$"
)
_DOTTED_STATE_KEY_PATTERN = r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$"


CAMPAIGN_PLAN_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:sylvia-gm:content:campaign-plan:2",
    "title": "Sylvia GM Campaign Plan v2",
    "description": (
        "Canonical private GM preparation for one campaign. The Plan binds exact Campaign "
        "Setting and Ruleset package copies, narrows party selection, and declares narrative "
        "and optional Ruleset-owned mechanical starting state. Structured records are closed; "
        "explicitly open state values are interpreted by the bound Ruleset."
    ),
    "type": "object",
    "additionalProperties": False,
    "required": [
        "bindings",
        "boundaries",
        "completion_conditions",
        "continuation_conditions",
        "failure_conditions",
        "format_id",
        "format_version",
        "gm_guidance",
        "id",
        "initial_state",
        "intended_player_experience",
        "objectives",
        "premise",
        "revision",
        "secrets",
        "setting_references",
        "side_content",
        "starting_location",
        "starting_party",
        "structure",
        "title",
        "tone",
        "unsupported_mechanics",
    ],
    "properties": {
        "format_id": {
            "const": "sylvia-gm-campaign-plan",
            "description": "Stable discriminator for the Campaign Plan artifact format.",
        },
        "format_version": {
            "type": "integer",
            "const": 2,
            "description": "Version of this structured Campaign Plan format.",
        },
        "id": {
            "$ref": "#/$defs/identity",
            "description": "Stable logical Plan identity used to derive the saved artifact ID.",
        },
        "revision": {
            "type": "integer",
            "minimum": 1,
            "description": "Positive author-controlled revision of this logical Plan.",
        },
        "title": {
            "$ref": "#/$defs/shortText",
            "description": "Human-readable Campaign Plan title.",
        },
        "premise": {
            "$ref": "#/$defs/text",
            "description": "The campaign's central fictional premise.",
        },
        "tone": {
            "$ref": "#/$defs/toneText",
            "description": "Concise tonal direction for the GM.",
        },
        "boundaries": {
            "$ref": "#/$defs/textList256",
            "description": "Zero or more campaign-specific content boundaries.",
        },
        "intended_player_experience": {
            "$ref": "#/$defs/text",
            "description": "The play experience this Plan is designed to support.",
        },
        "opening_direction": {
            "$ref": "#/$defs/text4000",
            "description": (
                "Optional private creative direction for the opening exchange. It guides pacing "
                "and presentation but does not establish facts, force a scene, or override rules."
            ),
        },
        "bindings": {"$ref": "#/$defs/bindings"},
        "starting_party": {"$ref": "#/$defs/startingParty"},
        "starting_location": {
            "$ref": "#/$defs/settingReference",
            "description": "Exact Campaign Setting entry where play begins.",
        },
        "initial_state": {"$ref": "#/$defs/initialState"},
        "setting_references": {"$ref": "#/$defs/settingReferenceGroups"},
        "structure": {
            "type": "array",
            "minItems": 1,
            "maxItems": 64,
            "items": {"$ref": "#/$defs/campaignAct"},
            "description": "One to 64 prepared acts; acts are opportunities, not a forced script.",
        },
        "secrets": {
            "type": "array",
            "maxItems": 256,
            "items": {"$ref": "#/$defs/secret"},
            "description": "Private Plan facts or references to private Setting knowledge.",
        },
        "objectives": {
            "type": "array",
            "minItems": 0,
            "items": {"$ref": "#/$defs/objective"},
            "description": "Campaign objectives with observable completion conditions.",
        },
        "gm_guidance": {"$ref": "#/$defs/gmGuidance"},
        "completion_conditions": {
            "$ref": "#/$defs/textList256",
            "description": "Conditions under which the campaign is complete.",
        },
        "continuation_conditions": {
            "$ref": "#/$defs/textList256",
            "description": "Conditions that support continued play beyond prepared material.",
        },
        "ending": {
            "description": (
                "Declare for every newly authored Plan: finite intended outcomes, or an explicit "
                "ongoing final-act note. Omission preserves older accepted snapshots only."
            ),
            "oneOf": [
                {
                    "type": "object", "additionalProperties": False,
                    "required": ["mode", "outcomes"],
                    "properties": {
                        "mode": {"const": "finite"},
                        "outcomes": {
                            "type": "array", "minItems": 1, "maxItems": 32,
                            "items": {"$ref": "#/$defs/intendedEnding"},
                        },
                    },
                },
                {
                    "type": "object", "additionalProperties": False,
                    "required": ["mode", "note"],
                    "properties": {
                        "mode": {"const": "ongoing"},
                        "note": {
                            "$ref": "#/$defs/text4000",
                            "description": "Final-act footnote declaring no intended conclusion.",
                        },
                    },
                },
            ],
        },
        "failure_conditions": {
            "$ref": "#/$defs/textList256",
            "description": "Conditions and outcomes that count as campaign failure.",
        },
        "side_content": {
            "$ref": "#/$defs/textList256",
            "description": "Optional side situations, leads, or other free-form preparation.",
        },
        "unsupported_mechanics": {
            "$ref": "#/$defs/textList256",
            "description": (
                "Mechanics needed by the Plan that the bound Ruleset does not encode and therefore "
                "require explicit GM adjudication. This list must not claim engine support."
            ),
        },
    },
    "$defs": {
        "intendedEnding": {
            "type": "object", "additionalProperties": False,
            "required": [
                "id", "conditions", "outcome", "immediate_aftermath", "open_world", "after_story",
            ],
            "properties": {
                "id": {"$ref": "#/$defs/identity"},
                "conditions": {
                    "type": "array", "minItems": 1, "maxItems": 64,
                    "items": {"$ref": "#/$defs/text4000"},
                    "description": (
                        "Played facts selecting this ending; their truth is GM judgment."
                    ),
                },
                "requires_all_acts": {
                    "type": "boolean",
                    "description": (
                        "Defaults to true when omitted. False declares an authored early ending "
                        "whose conditions and immediate aftermath can occur while acts remain "
                        "unfinished; recording it never completes those acts or grants awards."
                    ),
                },
                "outcome": {
                    "$ref": "#/$defs/text4000",
                    "description": "Concise outcome premise, not prewritten narration.",
                },
                "immediate_aftermath": {
                    "type": "array", "maxItems": 64,
                    "items": {"$ref": "#/$defs/text4000"},
                    "description": "Named loose-end resolutions before the completion card.",
                },
                "open_world": {
                    "type": "boolean", "description": "Whether this ending permits continued play.",
                },
                "after_story": {
                    "type": ["string", "null"], "minLength": 1, "maxLength": 4000,
                    "description": "Longer-future premise; narrate only after Finish the story.",
                },
            },
        },
        "identity": {
            "type": "string",
            "minLength": 1,
            "maxLength": 160,
            "pattern": _IDENTITY_PATTERN,
        },
        "nonblankString": {
            "type": "string",
            "minLength": 1,
            "pattern": r"^(?=\S(?:[\s\S]*\S)?$)(?![\s\S]*\x00)[\s\S]+$",
        },
        "text": {"allOf": [{"$ref": "#/$defs/nonblankString"}], "maxLength": 20000},
        "text4000": {"allOf": [{"$ref": "#/$defs/nonblankString"}], "maxLength": 4000},
        "shortText": {"allOf": [{"$ref": "#/$defs/nonblankString"}], "maxLength": 160},
        "toneText": {"allOf": [{"$ref": "#/$defs/nonblankString"}], "maxLength": 500},
        "textList128": {
            "type": "array",
            "maxItems": 128,
            "items": {"$ref": "#/$defs/text4000"},
        },
        "textList256": {
            "type": "array",
            "maxItems": 256,
            "items": {"$ref": "#/$defs/text4000"},
        },
        "portablePath": {
            "type": "string",
            "minLength": 1,
            "maxLength": 512,
            "pattern": _PORTABLE_PATH_PATTERN,
            "description": (
                "Forward-slash package-relative path with no drive, aliases, or padded segment."
            ),
        },
        "sha256": {
            "type": "string",
            "pattern": "^[0-9a-f]{64}$",
            "description": "Lowercase hexadecimal SHA-256 digest.",
        },
        "packageBinding": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "version", "content_sha256"],
            "properties": {
                "id": {"$ref": "#/$defs/identity", "description": "Exact package identity."},
                "version": {"$ref": "#/$defs/identity", "description": "Exact package version."},
                "content_sha256": {"$ref": "#/$defs/sha256"},
            },
        },
        "bindings": {
            "type": "object",
            "additionalProperties": False,
            "required": ["ruleset", "campaign_setting", "package_closure_sha256"],
            "properties": {
                "ruleset": {
                    "$ref": "#/$defs/packageBinding",
                    "description": "Exact Ruleset copy contained through the selected Setting.",
                },
                "campaign_setting": {
                    "$ref": "#/$defs/packageBinding",
                    "description": "Exact Campaign Setting copy contained by this Plan.",
                },
                "package_closure_sha256": {
                    "$ref": "#/$defs/sha256",
                    "description": "Digest of the admitted complete package closure.",
                },
            },
        },
        "settingReference": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "path"],
            "properties": {
                "id": {"$ref": "#/$defs/identity", "description": "Stable content identity."},
                "path": {
                    "$ref": "#/$defs/portablePath",
                    "description": (
                        "Path within the bound Campaign Setting, without a setting/ prefix."
                    ),
                },
            },
        },
        "playerCharacterPolicy": {
            "oneOf": [
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["mode"],
                    "properties": {
                        "mode": {
                            "const": "compatible",
                            "description": "Player selects any compatible Character File.",
                        }
                    },
                },
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["mode", "character"],
                    "properties": {
                        "mode": {"const": "fixed"},
                        "character": {
                            "$ref": "#/$defs/settingReference",
                            "description": (
                                "Exact Setting-owned Character File required for the player."
                            ),
                        },
                    },
                },
            ]
        },
        "startingParty": {
            "type": "object",
            "additionalProperties": False,
            "required": ["player_character", "required_companions", "custom_companions"],
            "properties": {
                "player_character": {"$ref": "#/$defs/playerCharacterPolicy"},
                "required_companions": {
                    "type": "array",
                    "maxItems": 64,
                    "items": {"$ref": "#/$defs/settingReference"},
                    "description": (
                        "Exact Setting-owned Companion Character Files required by the Plan."
                    ),
                },
                "custom_companions": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["minimum", "maximum"],
                    "properties": {
                        "minimum": {"type": "integer", "minimum": 0, "maximum": 64},
                        "maximum": {"type": "integer", "minimum": 0, "maximum": 64},
                    },
                    "description": "Allowed count of additional compatible Companions.",
                },
            },
        },
        "jsonValue": {
            "description": (
                "Any JSON value; its meaning and permitted shape belong to the bound Ruleset."
            ),
        },
        "stateEntry": {
            "type": "object",
            "additionalProperties": False,
            "required": ["namespace", "key", "value"],
            "properties": {
                "namespace": {
                    "$ref": "#/$defs/nonblankString",
                    "description": "Bound Ruleset ID or one of its dot-prefixed subnamespaces.",
                },
                "key": {
                    "$ref": "#/$defs/nonblankString",
                    "description": (
                        "Ruleset-declared state key; its vocabulary is Ruleset-specific."
                    ),
                },
                "value": {"$ref": "#/$defs/jsonValue"},
            },
        },
        "participantReference": {
            "type": "object",
            "additionalProperties": False,
            "required": ["$participant"],
            "properties": {"$participant": {"enum": ["id", "name"]}},
            "description": (
                "Runtime substitution for the assigned participant identity or display name."
            ),
        },
        "templateValue": {
            "anyOf": [
                {"$ref": "#/$defs/participantReference"},
                {
                    "type": "object",
                    "not": {"required": ["$participant"]},
                    "additionalProperties": {"$ref": "#/$defs/templateValue"},
                },
                {"type": "array", "items": {"$ref": "#/$defs/templateValue"}},
                {"type": ["string", "number", "integer", "boolean", "null"]},
            ]
        },
        "partyAppend": {
            "type": "object",
            "additionalProperties": False,
            "required": ["namespace", "key", "value"],
            "properties": {
                "namespace": {
                    "$ref": "#/$defs/nonblankString",
                    "description": "Bound Ruleset ID or one of its dot-prefixed subnamespaces.",
                },
                "key": {
                    "type": "string",
                    "pattern": _DOTTED_STATE_KEY_PATTERN,
                    "description": "Dot path to an existing Ruleset-owned list.",
                },
                "value": {"$ref": "#/$defs/templateValue"},
            },
        },
        "partyPlacement": {
            "type": "object",
            "additionalProperties": False,
            "required": ["entity_state", "append"],
            "properties": {
                "entity_state": {
                    "type": "object",
                    "not": {"required": ["$participant"]},
                    "additionalProperties": {"$ref": "#/$defs/templateValue"},
                    "description": (
                        "Ruleset-specific top-level replacements in copied character state."
                    ),
                },
                "append": {
                    "type": "array",
                    "maxItems": 16,
                    "items": {"$ref": "#/$defs/partyAppend"},
                    "description": (
                        "Ruleset-specific values appended to already-authored state lists."
                    ),
                },
            },
        },
        "entity": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "kind", "source_ref", "state"],
            "properties": {
                "id": {"$ref": "#/$defs/identity"},
                "kind": {
                    "$ref": "#/$defs/identity",
                    "description": "Author-defined narrative entity category.",
                },
                "source_ref": {"$ref": "#/$defs/settingReference"},
                "state": {
                    "type": "object",
                    "additionalProperties": True,
                    "description": "Open narrative starting data for this entity.",
                },
            },
        },
        "factRecord": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "fact", "known_by"],
            "properties": {
                "id": {"$ref": "#/$defs/identity"},
                "fact": {"$ref": "#/$defs/text"},
                "known_by": {
                    "type": "array",
                    "maxItems": 128,
                    "items": {"$ref": "#/$defs/text4000"},
                    "description": "Free-form knowledge holders; interpretation is narrative.",
                },
            },
        },
        "initialState": {
            "type": "object",
            "additionalProperties": False,
            "required": ["entities", "facts"],
            "properties": {
                "entities": {"type": "array", "maxItems": 128, "items": {"$ref": "#/$defs/entity"}},
                "facts": {
                    "type": "array",
                    "maxItems": 256,
                    "items": {"oneOf": [{"$ref": "#/$defs/text"}, {"$ref": "#/$defs/factRecord"}]},
                },
                "mechanics": {
                    "type": "array",
                    "maxItems": 256,
                    "items": {"$ref": "#/$defs/stateEntry"},
                    "description": (
                        "Optional exact Ruleset-owned starting state; contents are "
                        "Ruleset-specific."
                    ),
                },
                "party_placements": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 64,
                    "items": {"$ref": "#/$defs/partyPlacement"},
                    "description": (
                        "Ordered player-first templates for placing the selected party in "
                        "Ruleset state."
                    ),
                },
            },
        },
        "settingReferenceGroups": {
            "type": "object",
            "additionalProperties": False,
            "required": ["creatures", "factions", "items", "locations", "lore", "npcs"],
            "properties": {
                name: {
                    "type": "array",
                    "items": {"$ref": "#/$defs/settingReference"},
                    "description": f"Typed references to {name} in the bound Campaign Setting.",
                }
                for name in ("creatures", "factions", "items", "locations", "lore", "npcs")
            },
        },
        "campaignAct": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "id",
                "title",
                "summary",
                "situations",
                "objectives",
                "branches",
                "consequences",
                "planned_encounters",
                "dynamic_encounter_guidance",
                "setting_refs",
            ],
            "properties": {
                "id": {"$ref": "#/$defs/identity"},
                "title": {"$ref": "#/$defs/shortText"},
                "summary": {"$ref": "#/$defs/text"},
                "situations": {"$ref": "#/$defs/textList128"},
                "objectives": {
                    "type": "array",
                    "minItems": 0,
                    "maxItems": 128,
                    "uniqueItems": True,
                    "items": {"$ref": "#/$defs/identity"},
                    "description": (
                        "Declared objective IDs required to finish this act. All linked "
                        "objectives are required; keep optional work in situations or side_content."
                    ),
                },
                "branches": {"$ref": "#/$defs/textList128"},
                "consequences": {"$ref": "#/$defs/textList128"},
                "planned_encounters": {
                    "$ref": "#/$defs/textList128",
                    "description": (
                        "Free-form planned encounter preparation; mechanical details "
                        "remain Ruleset-specific."
                    ),
                },
                "dynamic_encounter_guidance": {
                    "$ref": "#/$defs/textList128",
                    "description": (
                        "Guidance for situational encounters using only available Ruleset "
                        "mechanics."
                    ),
                },
                "setting_refs": {
                    "type": "array",
                    "maxItems": 128,
                    "items": {"$ref": "#/$defs/portablePath"},
                    "description": "Campaign Setting paths relevant to this act.",
                },
            },
        },
        "reveal": {
            "type": "object",
            "additionalProperties": False,
            "required": ["condition", "from_act"],
            "properties": {
                "condition": {"$ref": "#/$defs/text"},
                "from_act": {
                    "$ref": "#/$defs/identity",
                    "description": (
                        "ID of the earliest declared act from which revelation is possible."
                    ),
                },
            },
        },
        "localSecret": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "fact", "known_by", "reveal"],
            "properties": {
                "id": {"$ref": "#/$defs/identity"},
                "fact": {"$ref": "#/$defs/text"},
                "known_by": {
                    "type": "array",
                    "maxItems": 128,
                    "items": {"$ref": "#/$defs/text4000"},
                },
                "reveal": {"$ref": "#/$defs/reveal"},
            },
        },
        "settingSecret": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "setting_fact_id", "reveal"],
            "properties": {
                "id": {"$ref": "#/$defs/identity"},
                "setting_fact_id": {
                    "$ref": "#/$defs/identity",
                    "description": "Exact private knowledge fact ID from the bound Setting.",
                },
                "reveal": {"$ref": "#/$defs/reveal"},
            },
        },
        "secret": {
            "oneOf": [
                {"$ref": "#/$defs/text"},
                {"$ref": "#/$defs/localSecret"},
                {"$ref": "#/$defs/settingSecret"},
            ]
        },
        "objective": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "title", "description", "completion_conditions"],
            "properties": {
                "id": {"$ref": "#/$defs/identity"},
                "title": {"$ref": "#/$defs/shortText"},
                "description": {"$ref": "#/$defs/text"},
                "completion_conditions": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 64,
                    "items": {"$ref": "#/$defs/text4000"},
                    "description": (
                        "All entries must be met by played facts, including the exact closing "
                        "event. State alternatives within the condition they satisfy. Name "
                        "required discoveries/events and any concrete failure ending."
                    ),
                },
            },
        },
        "gmGuidance": {
            "type": "object",
            "additionalProperties": False,
            "required": ["instructions", "player_freedom", "preparation_detail", "railroading"],
            "properties": {
                "instructions": {"$ref": "#/$defs/text"},
                "player_freedom": {"$ref": "#/$defs/text"},
                "preparation_detail": {"$ref": "#/$defs/text"},
                "railroading": {"$ref": "#/$defs/text"},
            },
        },
    },
}


EXAMPLE: dict[str, object] = {
    "format_id": "sylvia-gm-campaign-plan",
    "format_version": 2,
    "id": "signal-relay",
    "revision": 1,
    "title": "Signal Relay",
    "premise": "A relay begins returning a signal that nobody transmitted.",
    "tone": "Quiet science fiction mystery",
    "boundaries": [],
    "intended_player_experience": "Investigate an uncertain situation and choose what it becomes.",
    "bindings": {
        "ruleset": {"id": "example.signal-rules", "version": "1.0.0", "content_sha256": "1" * 64},
        "campaign_setting": {
            "id": "example.signal-setting",
            "version": "1.0.0",
            "content_sha256": "2" * 64,
        },
        "package_closure_sha256": "3" * 64,
    },
    "starting_party": {
        "player_character": {"mode": "compatible"},
        "required_companions": [],
        "custom_companions": {"minimum": 0, "maximum": 0},
    },
    "starting_location": {"id": "relay", "path": "locations/relay.md"},
    "initial_state": {
        "entities": [],
        "facts": ["The relay has just received an impossible reply."],
    },
    "setting_references": {
        "creatures": [],
        "factions": [],
        "items": [],
        "locations": [],
        "lore": [],
        "npcs": [],
    },
    "structure": [
        {
            "id": "act-1",
            "title": "The reply",
            "summary": "The player investigates the signal and decides whether to answer.",
            "situations": [
                "The reply repeats with one changed word. The local recorder identifies its "
                "source as the station's disconnected rescue beacon; comparing timestamps "
                "shows it is replaying an old distress call, not a live survivor.",
                "The player can reconnect the beacon, isolate it, or leave it unchanged. The "
                "duty operator can witness the controls and receive the recorder comparison.",
            ],
            "objectives": ["understand-signal"],
            "branches": ["Answer, isolate the relay, or leave it broadcasting."],
            "consequences": ["The chosen response changes who can detect the relay."],
            "planned_encounters": [],
            "dynamic_encounter_guidance": ["Use only operations exposed by the bound Ruleset."],
            "setting_refs": ["locations/relay.md"],
        }
    ],
    "secrets": [],
    "objectives": [
        {
            "id": "understand-signal",
            "title": "Understand the signal",
            "description": "Identify the repeated distress call and settle the beacon's operation.",
            "completion_conditions": [
                "The recorder and timestamps establish the disconnected rescue beacon as "
                "the source and the message as an old recording, not a live survivor.",
                "The player reconnects or isolates the beacon, or explicitly leaves its "
                "current setting unchanged; the actual control state is recorded.",
                "Closing event: the duty operator receives the comparison and the chosen "
                "control state, and acknowledges the report.",
            ],
        }
    ],
    "gm_guidance": {
        "instructions": (
            "Treat the Setting as world authority and the Ruleset as mechanical authority."
        ),
        "player_freedom": "Let the player's response determine the campaign's direction.",
        "preparation_detail": "Retrieve only the Setting entries needed for the current situation.",
        "railroading": "Preserve the premise without forcing a particular answer.",
    },
    "completion_conditions": [
        "The signal source, recording date and actual control decision are established; "
        "the duty operator has acknowledged the report."
    ],
    "continuation_conditions": ["Consequences of the response can support later play."],
    "ending": {
        "mode": "finite",
        "outcomes": [{
            "id": "signal-settled",
            "conditions": [
                "The beacon's source and chosen control state are established and the duty "
                "operator has acknowledged the report.",
            ],
            "outcome": "The false distress call is understood and its operation is settled.",
            "immediate_aftermath": [
                "The operator acknowledges the player's actual control decision and records "
                "who will maintain the beacon, or that it remains isolated.",
            ],
            "open_world": True,
            "after_story": None,
        }],
    },
    "failure_conditions": [],
    "side_content": [],
    "unsupported_mechanics": [],
}


FORMAT_NOTES: tuple[str, ...] = (
    (
        "Every new Plan declares ending: finite intended outcomes or an ongoing final-act note. "
        "The ongoing final act alone has no required objectives. Finite endings separate the "
        "immediate aftermath from an optional after-story, narrated only after the player chooses "
        "Finish the story. Open-world viability is a boolean, not a second story requirement."
        " Ordinary outcomes require all finite acts and linked objectives resolved. An authored "
        "early outcome may explicitly set requires_all_acts false; its actual conditions and "
        "immediate aftermath still require GM judgment, and unplayed material stays unfinished."
    ),
    (
        "Each finite act needs at least one unique reference to a declared objective. All linked "
        "objectives and all of each objective's completion_conditions are required; an "
        "explicit alternative satisfies only its own condition. Include the named closing "
        "event. Side work cannot substitute for required objectives. Structural validation "
        "checks links and text presence, not narrative specificity or whether play met them."
    ),
    (
        "JSON Schema cannot prove that either package binding or package_closure_sha256 "
        "equals the admitted exact package closure; publication supplies and compares that"
        " authority."
    ),
    (
        "Every Setting path must exist in the contained Campaign Setting, and the "
        "aggregate of party, starting-location, entity, and typed Setting references may "
        "not exceed 512."
    ),
    (
        "Required companion (id, path), entity IDs, fact-record IDs, act IDs, secret IDs, "
        "objective IDs, and mechanical (namespace, key) pairs must be unique where the "
        "runtime validator requires uniqueness."
    ),
    (
        "custom_companions.minimum must be no greater than maximum; JSON Schema expresses "
        "each bound but the shared procedural validator compares them."
    ),
    (
        "A setting-secret id must equal setting_fact_id and that fact must exist in "
        "admitted private Campaign Setting knowledge; reveal.from_act must name a declared"
        " act."
    ),
    (
        "Mechanical namespaces and party-placement append namespaces must equal the bound "
        "Ruleset ID or begin with that ID plus a dot; their keys and values must satisfy "
        "that Ruleset's declarations."
    ),
    (
        "Plan snapshot admission validates explicitly authored spatial scenes selected "
        "by the contained Ruleset's tactical-grid surface. Missing scenes may be created "
        "during play. This check does not validate later party-placement geometry."
    ),
    (
        "Party-placement templates, open entity state, and mechanical values must contain "
        "only safe finite JSON and no reserved canonical/private channels; the recursive "
        "runtime safety scan also rejects credential-shaped keys and absolute paths "
        "anywhere in the Plan."
    ),
    (
        "The canonical encoded Plan and its separate nonblank readable Markdown rendering "
        "are each limited to 512 KiB; the readable rendering is required by publication "
        "but is not a field in this JSON object."
    ),
    (
        "A successful structural check does not prove narrative quality, Setting truth, "
        "Ruleset capability, complete playable Character Files, calculated mechanics, or "
        "correctness of every possible mechanical sequence."
    ),
)
