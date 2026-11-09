"""Retrievable definitions for the existing package archive formats."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sylvia_gm.domain._content_schema_panel import PANEL_DECLARATION_SCHEMA

# Materialization expands every compact panel default before a package is admitted.
_PACKAGED_PANEL = deepcopy(PANEL_DECLARATION_SCHEMA)
_PACKAGED_PANEL["$id"] = "urn:sylvia-gm:content:packaged-panel:1"
_PACKAGED_PANEL["title"] = "Materialized Ruleset panel declaration"
_PACKAGED_PANEL["required"] = list(_PACKAGED_PANEL["properties"])

_TEXT = {"type": "string", "minLength": 1}
_SHA = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
_FILE = {
    "type": "object",
    "required": ["path", "bytes", "sha256"],
    "properties": {
        "path": _TEXT,
        "bytes": {"type": "integer", "minimum": 0},
        "sha256": _SHA,
    },
    "additionalProperties": False,
}
_DEPENDENCY: dict[str, Any] = {
    "type": "object",
    "required": ["namespace", "kind", "id", "version", "content_sha256"],
    "properties": {
        "namespace": _TEXT,
        "kind": _TEXT,
        "id": _TEXT,
        "version": _TEXT,
        "content_sha256": _SHA,
    },
    "additionalProperties": False,
}
_SURFACE = {
    "type": "object",
    "required": ["id", "capability"],
    "properties": {"id": _TEXT, "capability": _TEXT},
    "additionalProperties": False,
}
_TOOL = {
    "type": "object",
    "required": [
        "name",
        "capability",
        "input_schema",
        "state_predicates",
        "turn_statuses",
        "writers",
    ],
    "properties": {
        "name": _TEXT,
        "capability": _TEXT,
        "input_schema": {"type": "object", "additionalProperties": True},
        "state_predicates": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["namespace", "key", "equals"],
                "properties": {
                    "namespace": _TEXT,
                    "key": _TEXT,
                    "equals": {},
                    "negated": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
        },
        "turn_statuses": {"type": "array", "items": {"enum": ["open", "completed"]}},
        "writers": {"type": "array", "items": {"enum": ["builder", "dm"]}},
    },
    "additionalProperties": False,
}
_PARTICIPANT = {
    "type": "object",
    "required": ["kind", "schema_id", "schema_sha256", "schema"],
    "properties": {
        "kind": _TEXT,
        "schema_id": _TEXT,
        "schema_sha256": _SHA,
        "schema": {"type": "object", "additionalProperties": True},
        "entity_state_schema": {"type": "object", "additionalProperties": True},
        "initial_state_validation": {
            "type": "object", "required": ["description", "definitions", "effects"],
            "properties": {
                "description": {"type": "string", "minLength": 1, "maxLength": 2000},
                "definitions": {"type": "object"},
                "effects": {"type": "array", "minItems": 1, "maxItems": 1024,
                            "items": {"type": "object"}},
            },
            "additionalProperties": False,
        },
        "initial_state_calculations": {
            "type": "array",
            "maxItems": 128,
            "items": {
                "type": "object",
                "required": ["set"],
                "properties": {
                    "condition": {"type": "string", "minLength": 1, "maxLength": 500},
                    "set": {
                        "type": "object",
                        "required": ["field", "value"],
                        "properties": {
                            "field": _TEXT,
                            "value": {"type": "string", "minLength": 1, "maxLength": 500},
                        },
                        "additionalProperties": False,
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
    "dependentRequired": {
        "entity_state_schema": ["initial_state_calculations"],
        "initial_state_calculations": ["entity_state_schema"],
        "initial_state_validation": ["entity_state_schema", "initial_state_calculations"],
    },
}

PACKAGE_RECORD_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://pneuma.io/sylvia-gm/package-record.schema.json",
    "title": "Sylvia GM package record (archive formats 1-4)",
    "type": "object",
    "required": [
        "id",
        "version",
        "role",
        "metadata",
        "provenance",
        "compatibility",
        "minimum_model_capability_tier",
        "dependencies",
        "capabilities",
        "tools",
        "prompt_fragments",
        "ui_surfaces",
        "files",
        "content_sha256",
    ],
    "properties": {
        "id": _TEXT,
        "version": _TEXT,
        "role": {"enum": ["primary", "dependency"]},
        "content_kind": {"enum": ["ruleset", "campaign_setting", "support"]},
        "metadata": {
            "type": "object",
            "required": ["label"],
            "properties": {"label": _TEXT},
            "additionalProperties": False,
        },
        "provenance": {
            "type": "object",
            "required": ["publisher", "source_sha256"],
            "properties": {"publisher": _TEXT, "source_sha256": _SHA},
            "additionalProperties": False,
        },
        "compatibility": {
            "type": "object",
            "required": ["host_contract", "runtime_format", "requires"],
            "properties": {
                "host_contract": {"const": 1},
                "runtime_format": {"const": 1},
                "requires": {"type": "array", "items": _TEXT, "uniqueItems": True},
            },
            "additionalProperties": False,
        },
        "minimum_model_capability_tier": _TEXT,
        "dependencies": {"type": "array", "items": _DEPENDENCY},
        "capabilities": {"type": "array", "items": _TEXT, "uniqueItems": True},
        "tools": {"type": "array", "items": _TOOL},
        "prompt_fragments": {"type": "array", "items": _SURFACE},
        "ui_surfaces": {"type": "array", "items": _PACKAGED_PANEL},
        "participant_artifacts": {"type": "array", "items": _PARTICIPANT},
        "state_schemas": {
            "type": "array", "minItems": 1, "maxItems": 32,
            "items": {
                "type": "object", "required": ["key", "schema", "references"],
                "properties": {
                    "key": _TEXT,
                    "visibility": {"enum": ["public", "engine"], "default": "public"},
                    "required": {
                        "type": "boolean", "default": False,
                        "description": (
                            "Require this key before a Plan can be published or played. "
                            "Descendants retain this obligation."
                        ),
                    },
                    "schema": {"type": "object", "additionalProperties": True},
                    "references": {
                        "type": "array", "maxItems": 128,
                        "items": {
                            "type": "object", "required": ["source", "target_key", "target"],
                            "properties": {
                                "source": {"type": "array", "minItems": 1, "maxItems": 16,
                                           "items": {**_TEXT, "maxLength": 128}},
                                "target_key": _TEXT,
                                "target": {"type": "array", "minItems": 1, "maxItems": 16,
                                           "items": {**_TEXT, "maxLength": 128}},
                            },
                            "additionalProperties": False,
                        },
                    },
                },
                "additionalProperties": False,
            },
        },
        "content_declarations": _TEXT,
        "files": {"type": "array", "items": _FILE},
        "content_sha256": _SHA,
    },
    "additionalProperties": False,
    "allOf": [
        {
            "if": {
                "properties": {"role": {"const": "primary"}},
                "required": ["role", "content_kind"],
            },
            "then": {"properties": {"content_kind": {"const": "ruleset"}}},
            "else": {
                "if": {"required": ["content_kind"]},
                "then": {"properties": {"content_kind": {"enum": ["campaign_setting", "support"]}}},
            },
        }
    ],
}

PACKAGE_ARCHIVE_MANIFEST_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://pneuma.io/sylvia-gm/package-archive-manifest.schema.json",
    "title": "Sylvia GM package archive manifest",
    "type": "object",
    "required": ["format", "packages"],
    "properties": {
        "format": {"type": "integer", "enum": [1, 2, 3, 4]},
        "packages": {"type": "array", "minItems": 1, "items": PACKAGE_RECORD_SCHEMA},
    },
    "additionalProperties": False,
    "allOf": [
        {
            "if": {"properties": {"format": {"const": 1}}, "required": ["format"]},
            "then": {
                "properties": {
                    "packages": {
                        "items": {
                            "not": {
                                "anyOf": [
                                    {"required": ["participant_artifacts"]},
                                    {"required": ["content_declarations"]},
                                    {"required": ["content_kind"]},
                                    {"required": ["state_schemas"]},
                                ]
                            }
                        }
                    }
                }
            },
        },
        {
            "if": {"properties": {"format": {"const": 2}}, "required": ["format"]},
            "then": {
                "properties": {
                    "packages": {
                        "items": {
                            "not": {
                                "anyOf": [
                                    {"required": ["content_declarations"]},
                                    {"required": ["content_kind"]},
                                    {"required": ["state_schemas"]},
                                ]
                            }
                        }
                    }
                }
            },
        },
        {
            "if": {"properties": {"format": {"const": 3}}, "required": ["format"]},
            "then": {
                "properties": {
                    "packages": {
                        "items": {
                            "required": ["participant_artifacts", "content_declarations"],
                            "not": {"anyOf": [{"required": ["content_kind"]},
                                              {"required": ["state_schemas"]}]},
                        }
                    }
                }
            },
        },
        {
            "if": {"properties": {"format": {"const": 4}}, "required": ["format"]},
            "then": {
                "properties": {
                    "packages": {
                        "items": {
                            "required": [
                                "participant_artifacts",
                                "content_declarations",
                                "content_kind",
                            ]
                        }
                    }
                }
            },
        },
    ],
}

PACKAGE_DRAFT_RECORD_SCHEMA: dict[str, Any] = {
    **PACKAGE_RECORD_SCHEMA,
    "$id": "https://pneuma.io/sylvia-gm/package-draft-record-v4.schema.json",
    "title": "Sylvia GM format-4 package draft record",
    "required": [
        name
        for name in PACKAGE_RECORD_SCHEMA["required"]
        if name not in {"files", "content_sha256"}
    ]
    + ["participant_artifacts", "content_declarations", "content_kind"],
    "properties": {
        **PACKAGE_RECORD_SCHEMA["properties"],
        "dependencies": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["namespace", "kind", "id", "version"],
                "properties": {**_DEPENDENCY["properties"]},
                "additionalProperties": False,
            },
        },
    },
}
PACKAGE_DRAFT_RECORD_SCHEMA["properties"].pop("files")
PACKAGE_DRAFT_RECORD_SCHEMA["properties"].pop("content_sha256")

PACKAGE_EXAMPLE: dict[str, Any] = {
    "format": 4,
    "packages": [
        {
            "id": "example.coin-rules",
            "version": "1.0.0",
            "role": "primary",
            "content_kind": "ruleset",
            "metadata": {"label": "Coin Rules"},
            "provenance": {"publisher": "Example author", "source_sha256": "0" * 64},
            "compatibility": {"host_contract": 1, "runtime_format": 1, "requires": []},
            "minimum_model_capability_tier": "sylvia.capability.t01",
            "dependencies": [],
            "capabilities": [],
            "tools": [],
            "prompt_fragments": [],
            "ui_surfaces": [],
            "participant_artifacts": [],
            "content_declarations": "content/content-declarations.json",
            "files": [],
            "content_sha256": "1" * 64,
        }
    ],
}

PACKAGE_FORMAT_NOTES = (
    (
        "Portable package labels are limited to 256 UTF-8 bytes; identity lists to "
        "512 UTF-8 bytes per entry. Preserve the admitted identity/path grammar. "
        "These byte limits differ from JSON Schema character counts."
    ),
    (
        "The outer ZIP contains canonical manifest.json and only "
        "packages/<package-id>/<indexed-path> members."
    ),
    (
        "The manifest format selects package-record versions 1-4; format 4 adds "
        "content_kind and requires content declarations."
    ),
    (
        "Records inside manifest.json include derived files and content_sha256 "
        "fields; materializers derive these rather than asking authors to calculate "
        "them."
    ),
    (
        "Admission rejects directories, symlinks, Unicode/case aliases, undeclared "
        "members, absent indexed files, and byte-count or SHA-256 mismatches."
    ),
    (
        "The parser owns identity, ordering, reference, closure, schema-subset, "
        "runtime, and content-declaration link checks beyond this structural schema."
    ),
)

__all__ = [
    "PACKAGE_RECORD_SCHEMA",
    "PACKAGE_DRAFT_RECORD_SCHEMA",
    "PACKAGE_ARCHIVE_MANIFEST_SCHEMA",
    "PACKAGE_EXAMPLE",
    "PACKAGE_FORMAT_NOTES",
]
