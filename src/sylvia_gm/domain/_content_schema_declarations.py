"""Retrievable definition for existing package content declarations."""

from __future__ import annotations

from typing import Any

_TEXT = {"type": "string", "minLength": 1}
_SHA = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
_STRINGS = {"type": "array", "items": _TEXT, "uniqueItems": True}
_INCLUSION_CLASSIFICATIONS = {
    "type": "array",
    "minItems": 1,
    "uniqueItems": True,
    "items": {
        "enum": [
            "authored_content",
            "official_licensed_content",
            "mechanically_derived_official",
            "sylvia_gm_implementation",
            "compatibility",
            "interpretation",
        ]
    },
}
_EXCLUSION_CLASSIFICATIONS = {
    "type": "array",
    "minItems": 1,
    "uniqueItems": True,
    "items": {
        "enum": [
            "compatibility",
            "interpretation",
            "legal_nonmechanical",
            "official_licensed_content",
            "unavailable_closed_content",
            "unsupported",
        ]
    },
}
_LINKS = {
    "type": "object",
    "required": [
        "artifacts",
        "capabilities",
        "mechanics",
        "procedures",
        "prompt_fragments",
        "schemas",
        "state_namespaces",
        "tools",
        "ui_surfaces",
    ],
    "properties": dict.fromkeys(
        (
            "artifacts",
            "capabilities",
            "mechanics",
            "procedures",
            "prompt_fragments",
            "schemas",
            "state_namespaces",
            "tools",
            "ui_surfaces",
        ),
        _STRINGS,
    ),
    "additionalProperties": False,
}
_SOURCE_REF = {
    "type": "object",
    "required": ["authority_id", "source_unit_id", "locator"],
    "properties": {
        "authority_id": _TEXT,
        "source_unit_id": _TEXT,
        "locator": {
            "oneOf": [
                {
                    "type": "object",
                    "required": ["kind", "page", "section_path", "derived_text_sha256"],
                    "properties": {
                        "kind": {"const": "pdf_page_section"},
                        "page": {"type": "integer", "minimum": 1, "maximum": 100000},
                        "section_path": {"type": "array", "minItems": 1, "items": _TEXT},
                        "derived_text_sha256": _SHA,
                    },
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "required": ["kind", "pages", "content_sha256"],
                    "properties": {
                        "kind": {"const": "pdf_page_range"},
                        "pages": {
                            "type": "array",
                            "minItems": 2,
                            "maxItems": 2,
                            "items": {"type": "integer", "minimum": 1, "maximum": 100000},
                        },
                        "content_sha256": _SHA,
                    },
                    "additionalProperties": False,
                },
            ]
        },
    },
    "additionalProperties": False,
}
_RECORD_COMMON = {
    "id": _TEXT,
    "kind": _TEXT,
    "family": _TEXT,
    "classifications": _INCLUSION_CLASSIFICATIONS,
    "sources": {"type": "array", "items": _SOURCE_REF},
    "links": _LINKS,
    "evidence": {
        "type": "object",
        "required": ["tests"],
        "properties": {"tests": _STRINGS},
        "additionalProperties": False,
    },
}
_INCLUSION = {
    "type": "object",
    "required": [
        "id",
        "kind",
        "family",
        "disposition",
        "classifications",
        "sources",
        "links",
        "evidence",
    ],
    "properties": {
        **_RECORD_COMMON,
        "disposition": {
            "enum": ["included_mechanical", "included_nonmechanical", "included_compatibility"]
        },
    },
    "additionalProperties": False,
}
_EXCLUSION = {
    "type": "object",
    "required": [
        "id",
        "kind",
        "family",
        "reason",
        "classifications",
        "sources",
        "within_declared_scope",
    ],
    "properties": {
        "id": _TEXT,
        "kind": _TEXT,
        "family": _TEXT,
        "reason": {
            "enum": [
                "unavailable",
                "unsupported",
                "intentionally_excluded",
                "superseded",
                "other_classified",
            ]
        },
        "classifications": _EXCLUSION_CLASSIFICATIONS,
        "sources": {"type": "array", "items": _SOURCE_REF},
        "within_declared_scope": {"const": True},
    },
    "additionalProperties": False,
}

CONTENT_DECLARATIONS_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://pneuma.io/sylvia-gm/package-content-declarations-v1.schema.json",
    "title": "Sylvia GM package content declarations",
    "type": "object",
    "required": [
        "format_id",
        "format_version",
        "scope",
        "source_authorities",
        "inclusions",
        "exclusions",
    ],
    "properties": {
        "format_id": {"const": "sylvia-gm-package-content-declarations"},
        "format_version": {"const": 1},
        "scope": {
            "type": "object",
            "required": ["scope_id", "scope_kind", "claim_status", "source_boundary_refs"],
            "properties": {
                "scope_id": _TEXT,
                "scope_kind": {
                    "enum": ["development_tracer", "bounded_package", "complete_ruleset"]
                },
                "claim_status": {"enum": ["NOT_COMPLETE", "COMPLETE"]},
                "source_boundary_refs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["artifact_id", "sha256"],
                        "properties": {"artifact_id": _TEXT, "sha256": _SHA},
                        "additionalProperties": False,
                    },
                },
            },
            "additionalProperties": False,
        },
        "source_authorities": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": [
                    "id",
                    "document_id",
                    "version",
                    "media_type",
                    "content_sha256",
                    "license_id",
                ],
                "properties": {
                    "id": _TEXT,
                    "document_id": _TEXT,
                    "version": _TEXT,
                    "media_type": _TEXT,
                    "content_sha256": _SHA,
                    "license_id": _TEXT,
                },
                "additionalProperties": False,
            },
        },
        "inclusions": {"type": "array", "items": _INCLUSION},
        "exclusions": {"type": "array", "items": _EXCLUSION},
    },
    "additionalProperties": False,
}

CONTENT_DECLARATIONS_EXAMPLE = {
    "format_id": "sylvia-gm-package-content-declarations",
    "format_version": 1,
    "scope": {
        "scope_id": "example.coin",
        "scope_kind": "bounded_package",
        "claim_status": "NOT_COMPLETE",
        "source_boundary_refs": [],
    },
    "source_authorities": [
        {
            "id": "example.coin.source",
            "document_id": "coin-rules",
            "version": "1.0.0",
            "media_type": "text/plain",
            "content_sha256": "0" * 64,
            "license_id": "CC0-1.0",
        }
    ],
    "inclusions": [],
    "exclusions": [],
}

CONTENT_DECLARATIONS_NOTES = (
    (
        "The file is canonical compact JSON with sorted keys, no trailing newline, "
        "unique members, finite values, and the parser's byte/depth/node/member "
        "limits."
    ),
    (
        "Arrays and identities whose parser requires canonical sorted uniqueness "
        "must be sorted and unique; source_authorities must be nonempty."
    ),
    (
        "The package admission parser verifies every artifact, capability, tool, "
        "surface, schema, state namespace, and source-authority reference against "
        "the owning package."
    ),
    (
        "COMPLETE is a bounded source-coverage claim checked by the existing "
        "procedural rules; it is not inferred from prose or test counts."
    ),
)

__all__ = [
    "CONTENT_DECLARATIONS_SCHEMA",
    "CONTENT_DECLARATIONS_EXAMPLE",
    "CONTENT_DECLARATIONS_NOTES",
]
