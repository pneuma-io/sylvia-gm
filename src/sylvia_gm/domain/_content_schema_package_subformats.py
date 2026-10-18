"""Definitions for package knowledge/support files and portable Plan bundle records."""

from __future__ import annotations

from typing import Any

_IDENTITY = {"type": "string", "minLength": 1}
_NONBLANK = {"type": "string", "minLength": 1, "pattern": r".*\S.*"}
_SHA = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
_PORTABLE_PATH = {"type": "string", "minLength": 1}

CAMPAIGN_SETTING_KNOWLEDGE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://pneuma.io/sylvia-gm/campaign-setting-knowledge-v1.schema.json",
    "title": "Sylvia GM Campaign Setting knowledge",
    "type": "object",
    "required": ["format_id", "format_version", "facts", "private_references"],
    "properties": {
        "format_id": {"const": "sylvia-gm-campaign-setting-knowledge"},
        "format_version": {"type": "integer", "const": 1},
        "facts": {
            "type": "array",
            "maxItems": 128,
            "items": {
                "type": "object",
                "required": ["id", "fact", "known_by", "source_path"],
                "properties": {
                    "id": _IDENTITY,
                    "fact": {**_NONBLANK, "description": "A bounded GM knowledge fact."},
                    "known_by": {
                        "type": "array",
                        "maxItems": 128,
                        "items": _IDENTITY,
                        "uniqueItems": True,
                    },
                    "source_path": _PORTABLE_PATH,
                },
                "additionalProperties": False,
            },
        },
        "private_references": {
            "type": "array",
            "maxItems": 256,
            "items": _PORTABLE_PATH,
            "uniqueItems": True,
        },
    },
    "additionalProperties": False,
}

CAMPAIGN_SETTING_KNOWLEDGE_EXAMPLE = {
    "format_id": "sylvia-gm-campaign-setting-knowledge",
    "format_version": 1,
    "facts": [
        {
            "id": "relay-purpose",
            "fact": "The relay was built to carry emergency messages.",
            "known_by": ["station-keeper"],
            "source_path": "people/station-keeper.md",
        }
    ],
    "private_references": ["people/station-keeper.md"],
}

CAMPAIGN_SETTING_KNOWLEDGE_NOTES = (
    "The canonical UTF-8 JSON file is limited to 256 KiB and parser depth/node/member limits.",
    (
        "Fact ids, known_by ids, fact records, and private references use authority identity/path "
        "validation; arrays must be sorted and unique."
    ),
    (
        "Each fact is limited to 4,000 UTF-8 bytes, has no surrounding whitespace, "
        "and names its package-owned source file."
    ),
    "Every source_path and private reference must occur in the same admitted package's file index.",
)

_SUPPORT_RECORD = {
    "type": "object",
    "required": [
        "id",
        "reference_availability",
        "gameplay_resolution",
        "mechanic_family",
        "deterministic_part",
        "gm_part",
    ],
    "properties": {
        "id": _IDENTITY,
        "reference_availability": {"enum": ["included", "excluded"]},
        "gameplay_resolution": {
            "enum": [
                "not_applicable",
                "engine_family",
                "engine_specialized",
                "hybrid",
                "gm_adjudicated",
                "unsupported",
            ]
        },
        "mechanic_family": {"oneOf": [{"type": "null"}, _IDENTITY]},
        "deterministic_part": {"oneOf": [{"type": "null"}, _NONBLANK]},
        "gm_part": {"oneOf": [{"type": "null"}, _NONBLANK]},
    },
    "additionalProperties": False,
    "allOf": [
        {
            "if": {
                "properties": {
                    "gameplay_resolution": {"enum": ["engine_family", "hybrid", "gm_adjudicated"]}
                },
                "required": ["gameplay_resolution"],
            },
            "then": {"properties": {"mechanic_family": {"type": "string", "minLength": 1}}},
        },
        {
            "if": {
                "properties": {"gameplay_resolution": {"const": "hybrid"}},
                "required": ["gameplay_resolution"],
            },
            "then": {"properties": {"deterministic_part": _NONBLANK, "gm_part": _NONBLANK}},
            "else": {
                "properties": {"deterministic_part": {"type": "null"}, "gm_part": {"type": "null"}}
            },
        },
        {
            "if": {
                "properties": {"reference_availability": {"const": "excluded"}},
                "required": ["reference_availability"],
            },
            "then": {
                "properties": {"gameplay_resolution": {"enum": ["not_applicable", "unsupported"]}}
            },
        },
    ],
}

GAMEPLAY_SUPPORT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://pneuma.io/sylvia-gm/gameplay-support-v1.schema.json",
    "title": "Sylvia GM gameplay support declarations",
    "type": "object",
    "required": ["format_id", "format_version", "records"],
    "properties": {
        "format_id": {"const": "sylvia-gm-gameplay-support"},
        "format_version": {"type": "integer", "const": 1},
        "records": {"type": "array", "maxItems": 10000, "items": _SUPPORT_RECORD},
    },
    "additionalProperties": False,
}

GAMEPLAY_SUPPORT_EXAMPLE = {
    "format_id": "sylvia-gm-gameplay-support",
    "format_version": 1,
    "records": [
        {
            "id": "negotiation-outcome",
            "reference_availability": "included",
            "gameplay_resolution": "hybrid",
            "mechanic_family": "social-resolution",
            "deterministic_part": "The engine records the declared stakes and check result.",
            "gm_part": "The GM applies the authored person's motives and decides the response.",
        }
    ],
}

GAMEPLAY_SUPPORT_NOTES = (
    "The canonical UTF-8 JSON file is limited to 2 MiB; records must be sorted and unique by id.",
    (
        "Identity fields use authority identity validation. Optional ownership text "
        "must be nonblank when present."
    ),
    (
        "engine_family, hybrid, and gm_adjudicated require mechanic_family; hybrid alone "
        "requires both ownership descriptions."
    ),
    "Excluded reference content may declare only not_applicable or unsupported resolution.",
)

PORTABLE_PLAN_BUNDLE_RECORD_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://pneuma.io/sylvia-gm/portable-plan-bundle-record-v3.schema.json",
    "title": "Sylvia GM portable Campaign Plan bundle record",
    "type": "object",
    "required": [
        "format_id",
        "format_version",
        "artifact_id",
        "content_sha256",
        "plan_content_sha256",
        "title",
        "summary",
        "source_plan_content_sha256",
        "setup_id",
        "package_snapshot",
        "package_snapshot_sha256",
    ],
    "properties": {
        "format_id": {"const": "sylvia-gm-reusable-campaign-plan"},
        "format_version": {"type": "integer", "const": 3},
        "artifact_id": {
            "type": "string",
            "pattern": "^campaign-plan-reusable-[0-9a-f]{24}-[0-9a-f]{16}$",
        },
        "content_sha256": _SHA,
        "plan_content_sha256": _SHA,
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "source_plan_content_sha256": _SHA,
        "setup_id": {
            "oneOf": [
                {"type": "null"},
                {"type": "string", "minLength": 1, "pattern": "^[^\\u0000]+$"},
            ]
        },
        "package_snapshot": {"const": "setting.sylvia-gm-package"},
        "package_snapshot_sha256": _SHA,
    },
    "additionalProperties": False,
}

PORTABLE_PLAN_BUNDLE_RECORD_EXAMPLE = {
    "format_id": "sylvia-gm-reusable-campaign-plan",
    "format_version": 3,
    "artifact_id": "campaign-plan-reusable-000000000000000000000000-0000000000000000",
    "content_sha256": "0" * 64,
    "plan_content_sha256": "1" * 64,
    "title": "The Silent Relay",
    "summary": "Find the lost signal.",
    "source_plan_content_sha256": "2" * 64,
    "setup_id": None,
    "package_snapshot": "setting.sylvia-gm-package",
    "package_snapshot_sha256": "3" * 64,
}

PORTABLE_PLAN_BUNDLE_RECORD_NOTES = (
    (
        "This is record.json inside an exported .sylvia-gm-plan bundle. Portable bundles "
        "require version 3; local legacy records use versions 1 and 2 and are not portable "
        "bundle authoring formats."
    ),
    (
        "record.json is limited to 64 KiB and must be canonical JSON. The ZIP has a closed, "
        "bounded regular-file member set and requires record.json, plan.json, plan.md, "
        "setting.sylvia-gm-package; interview-contribution.json is optional."
    ),
    (
        "The loader validates plan.json and plan.md against the retained package snapshot, "
        "checks catalog title/summary, and recomputes plan, snapshot, contribution, "
        "and aggregate hashes."
    ),
    (
        "setup_id is either null or the one supported built-in setup identity; that identity "
        "also requires its exact Ruleset and Setting bindings."
    ),
)

__all__ = [name for name in globals() if name.endswith(("_SCHEMA", "_EXAMPLE", "_NOTES"))]
