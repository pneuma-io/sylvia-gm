"""Retrievable structural definition for the existing Portable Campaign Export."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_TEXT = {"type": "string", "minLength": 1}
_SHA = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
_STRINGS = {"type": "array", "items": _TEXT, "uniqueItems": True}
_TABLES = (
    "campaigns",
    "campaign_lifecycle_state",
    "campaign_lifecycle_transitions",
    "campaign_operations",
    "campaign_constructions",
    "campaign_continuity_records",
    "campaign_revisions",
    "campaign_package_memberships",
    "participants",
    "participant_quarantines",
    "campaign_state_namespace_owners",
    "campaign_state",
    "turns",
    "actions",
    "idempotency_records",
    "canonical_events",
    "narration_interruptions",
    "mutations",
    "consequences",
    "campaign_locks",
    "campaign_lock_memberships",
    "package_activations",
    "campaign_plans",
    "builder_dm_handoffs",
    "agent_sessions",
    "pending_mechanics_actions",
    "pending_mechanics_action_aliases",
    "mechanics_choice_resolutions",
    "agent_session_messages",
    "package_candidates",
    "package_candidate_memberships",
    "package_staging",
    "packages",
    "package_dependencies",
    "package_manifests",
    "package_manifest_files",
    "package_exact_dependencies",
    "package_capabilities",
    "package_tools",
    "package_prompt_fragments",
    "package_ui_surfaces",
    "package_participant_artifacts",
    "package_blobs",
)

PORTABLE_CAMPAIGN_EXPORT_MANIFEST_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://pneuma.io/sylvia-gm/portable-campaign-export-v1.schema.json",
    "title": "Sylvia GM Portable Campaign Export manifest",
    "type": "object",
    "required": [
        "format_id",
        "format_version",
        "product_id",
        "product_generation",
        "source_authority_schema_version",
        "source_authority_schema_identity_sha256",
        "migration_provenance",
        "campaign_id",
        "captured_revision",
        "authority_record_count",
        "table_row_counts",
        "blob_count",
        "closure_index",
        "non_manifest_members",
    ],
    "properties": {
        "format_id": {"const": "sylvia-gm-campaign-export"},
        "format_version": {"const": 1},
        "product_id": {"const": "sylvia-gm"},
        "product_generation": {"const": "alpha"},
        "source_authority_schema_version": {"enum": [9, 10, 11, 12]},
        "source_authority_schema_identity_sha256": _SHA,
        "migration_provenance": {
            "type": "object",
            "required": ["schema_migrations", "schema_extensions"],
            "properties": {
                "schema_migrations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["version", "applied_name", "sql_sha256"],
                        "properties": {
                            "version": {"type": "integer", "minimum": 1},
                            "applied_name": _TEXT,
                            "sql_sha256": _SHA,
                        },
                        "additionalProperties": False,
                    },
                },
                "schema_extensions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": [
                            "extension_name",
                            "extension_sha256",
                            "predecessor_migration_name",
                            "predecessor_sql_sha256",
                        ],
                        "properties": {
                            "extension_name": _TEXT,
                            "extension_sha256": _SHA,
                            "predecessor_migration_name": _TEXT,
                            "predecessor_sql_sha256": _SHA,
                        },
                        "additionalProperties": False,
                    },
                },
            },
            "additionalProperties": False,
        },
        "campaign_id": _TEXT,
        "captured_revision": {"type": "integer", "minimum": 0},
        "authority_record_count": {"type": "integer", "minimum": 0},
        "table_row_counts": {
            "type": "object",
            "required": list(_TABLES),
            "properties": {name: {"type": "integer", "minimum": 0} for name in _TABLES},
            "additionalProperties": False,
        },
        "blob_count": {"type": "integer", "minimum": 0},
        "closure_index": {
            "type": "object",
            "required": [
                "lock_ids",
                "action_ids",
                "candidate_ids",
                "package_identities",
                "blob_sha256s",
                "plan_ids",
                "handoff_ids",
                "session_ids",
            ],
            "properties": {
                "lock_ids": _STRINGS,
                "action_ids": _STRINGS,
                "candidate_ids": _STRINGS,
                "package_identities": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["package_id", "version", "content_sha256"],
                        "properties": {
                            "package_id": _TEXT,
                            "version": _TEXT,
                            "content_sha256": _SHA,
                        },
                        "additionalProperties": False,
                    },
                },
                "blob_sha256s": {"type": "array", "items": _SHA, "uniqueItems": True},
                "plan_ids": _STRINGS,
                "handoff_ids": _STRINGS,
                "session_ids": _STRINGS,
            },
            "additionalProperties": False,
        },
        "non_manifest_members": {
            "type": "array",
            "not": {
                "contains": {
                    "properties": {"name": {"const": "provider-private.jsonl"}},
                    "required": ["name"],
                }
            },
            "items": {
                "type": "object",
                "required": ["name", "byte_count", "sha256"],
                "properties": {
                    "name": _TEXT,
                    "byte_count": {"type": "integer", "minimum": 0},
                    "sha256": _SHA,
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}

PORTABLE_CAMPAIGN_EXPORT_EXAMPLE = {
    "format_id": "sylvia-gm-campaign-export",
    "format_version": 1,
    "product_id": "sylvia-gm",
    "product_generation": "alpha",
    "source_authority_schema_version": 12,
    "source_authority_schema_identity_sha256": "0" * 64,
    "migration_provenance": {"schema_migrations": [], "schema_extensions": []},
    "campaign_id": "example-campaign",
    "captured_revision": 0,
    "authority_record_count": 0,
    "table_row_counts": dict.fromkeys(_TABLES, 0),
    "blob_count": 0,
    "closure_index": {
        "lock_ids": [],
        "action_ids": [],
        "candidate_ids": [],
        "package_identities": [],
        "blob_sha256s": [],
        "plan_ids": [],
        "handoff_ids": [],
        "session_ids": [],
    },
    "non_manifest_members": [{"name": "authority.jsonl", "byte_count": 0, "sha256": "1" * 64}],
}

PORTABLE_CAMPAIGN_EXPORT_NOTES = (
    (
        "This is the existing .sylvia-gm-campaign ZIP contract: authority.jsonl, "
        "blobs/<sha256>, then canonical manifest.json in exact member order."
    ),
    (
        "The manifest schema is structural. The codec remains authoritative for "
        "exact product/schema generations, migration provenance, table names and row "
        "schemas, canonical bytes, ordering, limits, hashes, counts, and closure "
        "indexes."
    ),
    (
        "Every non-manifest member is declared by exact name, size, and SHA-256; "
        "every retained package blob must agree with authority rows and package-file "
        "byte counts."
    ),
    (
        "Exports contain the selected campaign aggregate and exact package closure. "
        "They exclude Full Audit, credentials, independent Builder chats, unrelated "
        "campaigns, and workspace scratch."
    ),
    (
        "Recovery verifies into staging before the sole authority writer commits it; "
        "retrieving this definition does not export, recover, or inspect campaign "
        "data."
    ),
)

PORTABLE_CAMPAIGN_EXPORT_V2_MANIFEST_SCHEMA = deepcopy(PORTABLE_CAMPAIGN_EXPORT_MANIFEST_SCHEMA)
PORTABLE_CAMPAIGN_EXPORT_V2_MANIFEST_SCHEMA["$id"] = (
    "https://pneuma.io/sylvia-gm/portable-campaign-export-v2.schema.json"
)
PORTABLE_CAMPAIGN_EXPORT_V2_MANIFEST_SCHEMA["title"] = (
    "Sylvia GM Portable Campaign Export manifest v2 with saved thinking"
)
PORTABLE_CAMPAIGN_EXPORT_V2_MANIFEST_SCHEMA["properties"]["format_version"] = {"const": 2}
_private_members = PORTABLE_CAMPAIGN_EXPORT_V2_MANIFEST_SCHEMA["properties"]["non_manifest_members"]
_private_members["contains"] = _private_members.pop("not")["contains"]
PORTABLE_CAMPAIGN_EXPORT_V2_EXAMPLE = deepcopy(PORTABLE_CAMPAIGN_EXPORT_EXAMPLE)
PORTABLE_CAMPAIGN_EXPORT_V2_EXAMPLE["format_version"] = 2
PORTABLE_CAMPAIGN_EXPORT_V2_EXAMPLE["non_manifest_members"].append(
    {"name": "provider-private.jsonl", "byte_count": 0, "sha256": "2" * 64}
)
PORTABLE_CAMPAIGN_EXPORT_V2_NOTES = PORTABLE_CAMPAIGN_EXPORT_NOTES + (
    "This is the already-supported format 2, selected only when saved thinking is explicitly "
    "included. The provider-private.jsonl member is required and checked by the existing "
    "private-record codec; it is excluded from ordinary history. Format 1 excludes this member. "
    "No credentials or Full Audit are included in either format.",
)

__all__ = [
    "PORTABLE_CAMPAIGN_EXPORT_MANIFEST_SCHEMA",
    "PORTABLE_CAMPAIGN_EXPORT_EXAMPLE",
    "PORTABLE_CAMPAIGN_EXPORT_NOTES",
    "PORTABLE_CAMPAIGN_EXPORT_V2_MANIFEST_SCHEMA",
    "PORTABLE_CAMPAIGN_EXPORT_V2_EXAMPLE",
    "PORTABLE_CAMPAIGN_EXPORT_V2_NOTES",
]
