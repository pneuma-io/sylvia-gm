"""Versioned authoring definitions returned to Builder and external content authors."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sylvia_gm.domain._content_schema_campaign_export import (
    PORTABLE_CAMPAIGN_EXPORT_EXAMPLE,
    PORTABLE_CAMPAIGN_EXPORT_MANIFEST_SCHEMA,
    PORTABLE_CAMPAIGN_EXPORT_NOTES,
    PORTABLE_CAMPAIGN_EXPORT_V2_EXAMPLE,
    PORTABLE_CAMPAIGN_EXPORT_V2_MANIFEST_SCHEMA,
    PORTABLE_CAMPAIGN_EXPORT_V2_NOTES,
)
from sylvia_gm.domain._content_schema_character import CHARACTER_FILE_SCHEMA
from sylvia_gm.domain._content_schema_character import EXAMPLE as CHARACTER_FILE_EXAMPLE
from sylvia_gm.domain._content_schema_character import FORMAT_NOTES as CHARACTER_FILE_NOTES
from sylvia_gm.domain._content_schema_character_profile import CHARACTER_PROFILE_SCHEMA
from sylvia_gm.domain._content_schema_character_profile import EXAMPLE as CHARACTER_PROFILE_EXAMPLE
from sylvia_gm.domain._content_schema_character_profile import (
    FORMAT_NOTES as CHARACTER_PROFILE_NOTES,
)
from sylvia_gm.domain._content_schema_character_profile_v2 import (
    CHARACTER_PROFILE_V2_SCHEMA,
)
from sylvia_gm.domain._content_schema_character_profile_v2 import (
    EXAMPLE as CHARACTER_PROFILE_V2_EXAMPLE,
)
from sylvia_gm.domain._content_schema_character_profile_v2 import (
    FORMAT_NOTES as CHARACTER_PROFILE_V2_NOTES,
)
from sylvia_gm.domain._content_schema_declarations import (
    CONTENT_DECLARATIONS_EXAMPLE,
    CONTENT_DECLARATIONS_NOTES,
    CONTENT_DECLARATIONS_SCHEMA,
)
from sylvia_gm.domain._content_schema_map_card import (
    RESULT_CARD_EXAMPLE,
    RESULT_CARD_NOTES,
    RESULT_CARD_SCHEMA,
    SPATIAL_SCENE_EXAMPLE,
    SPATIAL_SCENE_NOTES,
    SPATIAL_SCENE_SCHEMA,
    TACTICAL_GRID_SOURCE_EXAMPLE,
    TACTICAL_GRID_SOURCE_NOTES,
    TACTICAL_GRID_SOURCE_SCHEMA,
)
from sylvia_gm.domain._content_schema_package import (
    PACKAGE_ARCHIVE_MANIFEST_SCHEMA,
    PACKAGE_DRAFT_RECORD_SCHEMA,
    PACKAGE_EXAMPLE,
    PACKAGE_FORMAT_NOTES,
)
from sylvia_gm.domain._content_schema_package_subformats import (
    CAMPAIGN_SETTING_KNOWLEDGE_EXAMPLE,
    CAMPAIGN_SETTING_KNOWLEDGE_NOTES,
    CAMPAIGN_SETTING_KNOWLEDGE_SCHEMA,
    GAMEPLAY_SUPPORT_EXAMPLE,
    GAMEPLAY_SUPPORT_NOTES,
    GAMEPLAY_SUPPORT_SCHEMA,
    PORTABLE_PLAN_BUNDLE_RECORD_EXAMPLE,
    PORTABLE_PLAN_BUNDLE_RECORD_NOTES,
    PORTABLE_PLAN_BUNDLE_RECORD_SCHEMA,
)
from sylvia_gm.domain._content_schema_panel import EXAMPLE as PANEL_DECLARATION_EXAMPLE
from sylvia_gm.domain._content_schema_panel import FORMAT_NOTES as PANEL_DECLARATION_NOTES
from sylvia_gm.domain._content_schema_panel import PANEL_DECLARATION_SCHEMA
from sylvia_gm.domain._content_schema_plan import (
    CAMPAIGN_PLAN_SCHEMA,
)
from sylvia_gm.domain._content_schema_plan import EXAMPLE as CAMPAIGN_PLAN_EXAMPLE
from sylvia_gm.domain._content_schema_plan import FORMAT_NOTES as CAMPAIGN_PLAN_NOTES
from sylvia_gm.domain._content_schema_ruleset import EXAMPLE as RULESET_SOURCE_EXAMPLE
from sylvia_gm.domain._content_schema_ruleset import FORMAT_NOTES as RULESET_SOURCE_NOTES
from sylvia_gm.domain._content_schema_ruleset import RULESET_SOURCE_SCHEMA
from sylvia_gm.domain._content_schema_ruleset_tests import (
    EXAMPLE as RULESET_TESTS_EXAMPLE,
)
from sylvia_gm.domain._content_schema_ruleset_tests import (
    FORMAT_NOTES as RULESET_TESTS_NOTES,
)
from sylvia_gm.domain._content_schema_ruleset_tests import (
    RULESET_TESTS_PATH,
    RULESET_TESTS_SCHEMA,
)
from sylvia_gm.domain._content_schema_runtime import (
    PACKAGE_RUNTIME_SCHEMA,
    RUNTIME_EXAMPLE,
    RUNTIME_NOTES,
    RUNTIME_PROCEDURAL_DEFINITION,
    SCHEMA_ADMISSION_SUBSET_SCHEMA,
    SCHEMA_SUBSET_EXAMPLE,
    SCHEMA_SUBSET_NOTES,
)
from sylvia_gm.domain._content_schema_setting import EXAMPLE as SETTING_REQUIREMENTS_EXAMPLE
from sylvia_gm.domain._content_schema_setting import FORMAT_NOTES as SETTING_REQUIREMENTS_NOTES
from sylvia_gm.domain._content_schema_setting import SETTING_CHARACTER_REQUIREMENTS_SCHEMA
from sylvia_gm.domain._content_schema_ui import (
    INTERVIEW_CONTRIBUTION_EXAMPLE,
    INTERVIEW_CONTRIBUTION_NOTES,
    INTERVIEW_CONTRIBUTION_SCHEMA,
    PANEL_LAYOUT_EXAMPLE,
    PANEL_LAYOUT_NOTES,
    PANEL_LAYOUT_SCHEMA,
)
from sylvia_gm.domain.inventory import inventory_holding_schema

_DEFINITIONS: dict[str, tuple[dict[str, Any], dict[str, Any], tuple[str, ...], str]] = {
    "portable_campaign_export_v2": (
        PORTABLE_CAMPAIGN_EXPORT_V2_MANIFEST_SCHEMA,
        PORTABLE_CAMPAIGN_EXPORT_V2_EXAMPLE,
        PORTABLE_CAMPAIGN_EXPORT_V2_NOTES,
        "manifest.json in .sylvia-gm-campaign with provider-private.jsonl",
    ),
    "character_profile_v2": (
        CHARACTER_PROFILE_V2_SCHEMA, CHARACTER_PROFILE_V2_EXAMPLE, CHARACTER_PROFILE_V2_NOTES,
        "character-profile-v2.json",
    ),
    "character_profile": (
        CHARACTER_PROFILE_SCHEMA, CHARACTER_PROFILE_EXAMPLE, CHARACTER_PROFILE_NOTES,
        "character-profile.json",
    ),
    "inventory_holding": (
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "sylvia-gm/inventory-holding/v1",
            "title": "Inventory holding",
            **inventory_holding_schema(),
        },
        {
            "holding_id": "mira-arrows", "item_id": "ordinary-arrow", "name": "Arrow",
            "kind": "item", "stackable": True, "quantity": 2, "owner_id": "waystation",
            "holder_id": "mira", "visibility": "player_known", "known_by": ["player"],
        },
        (
            "Owner and holder/location reference persistent campaign entities or participants.",
            "Quantities are whole units. Currency has a stable named identity; "
            "no exchange rate is inferred.",
            "Distinct objects have at most one unit across all holdings of their item_id.",
            "For a Ruleset holdings projection, use this schema as the inventory array items "
            "after removing only the $schema, $id and title documentation headers.",
            "Ruleset inventory selection requires x-sylvia-gm-inventory. Shared bookkeeping "
            "uses format string_list or holdings; mechanic-owned equipment uses format mechanics "
            "with its own state {namespace, key}, declared surface_id and tool names. The latter "
            "must not also declare character bookkeeping. A panel name alone selects nothing.",
        ),
        "Campaign State inventory holdings; Ruleset inventory field items",
    ),
    "ruleset_tests": (
        RULESET_TESTS_SCHEMA, RULESET_TESTS_EXAMPLE, RULESET_TESTS_NOTES, RULESET_TESTS_PATH,
    ),
    "campaign_setting_knowledge": (
        CAMPAIGN_SETTING_KNOWLEDGE_SCHEMA,
        CAMPAIGN_SETTING_KNOWLEDGE_EXAMPLE,
        CAMPAIGN_SETTING_KNOWLEDGE_NOTES,
        "setting-knowledge.json",
    ),
    "gameplay_support": (
        GAMEPLAY_SUPPORT_SCHEMA,
        GAMEPLAY_SUPPORT_EXAMPLE,
        GAMEPLAY_SUPPORT_NOTES,
        "support/gameplay-resolution.json",
    ),
    "portable_plan_bundle_record": (
        PORTABLE_PLAN_BUNDLE_RECORD_SCHEMA,
        PORTABLE_PLAN_BUNDLE_RECORD_EXAMPLE,
        PORTABLE_PLAN_BUNDLE_RECORD_NOTES,
        "record.json in .sylvia-gm-plan",
    ),
    "package_runtime": (
        PACKAGE_RUNTIME_SCHEMA,
        RUNTIME_EXAMPLE,
        RUNTIME_NOTES,
        "runtime/ruleset.yaml",
    ),
    "schema_admission_subset": (
        SCHEMA_ADMISSION_SUBSET_SCHEMA,
        SCHEMA_SUBSET_EXAMPLE,
        SCHEMA_SUBSET_NOTES,
        "Package tool, character and world-information schemas",
    ),
    "package_archive": (
        PACKAGE_ARCHIVE_MANIFEST_SCHEMA,
        PACKAGE_EXAMPLE,
        PACKAGE_FORMAT_NOTES,
        "manifest.json in .sylvia-gm-package",
    ),
    "package_draft_record": (
        PACKAGE_DRAFT_RECORD_SCHEMA,
        {
            key: value
            for key, value in PACKAGE_EXAMPLE["packages"][0].items()
            if key not in {"files", "content_sha256"}
        },
        PACKAGE_FORMAT_NOTES
        + (
            "Draft records omit derived file indexes and package hashes. The materializer "
            "computes these and missing dependency hashes from the supplied files and closure.",
        ),
        "package.json or package-records/<id>.json",
    ),
    "package_content_declarations": (
        CONTENT_DECLARATIONS_SCHEMA,
        CONTENT_DECLARATIONS_EXAMPLE,
        CONTENT_DECLARATIONS_NOTES,
        "content/content-declarations.json",
    ),
    "portable_campaign_export": (
        PORTABLE_CAMPAIGN_EXPORT_MANIFEST_SCHEMA,
        PORTABLE_CAMPAIGN_EXPORT_EXAMPLE,
        PORTABLE_CAMPAIGN_EXPORT_NOTES,
        "manifest.json in .sylvia-gm-campaign",
    ),
    "tactical_grid_source": (
        TACTICAL_GRID_SOURCE_SCHEMA,
        TACTICAL_GRID_SOURCE_EXAMPLE,
        TACTICAL_GRID_SOURCE_NOTES,
        "Ruleset tactical-grid panel source",
    ),
    "spatial_scene": (
        SPATIAL_SCENE_SCHEMA,
        SPATIAL_SCENE_EXAMPLE,
        SPATIAL_SCENE_NOTES,
        "Ruleset state: spatial scene",
    ),
    "result_card": (
        RESULT_CARD_SCHEMA,
        RESULT_CARD_EXAMPLE,
        RESULT_CARD_NOTES,
        "Ruleset tool metadata: result_card",
    ),
    "character_file": (
        CHARACTER_FILE_SCHEMA,
        CHARACTER_FILE_EXAMPLE,
        CHARACTER_FILE_NOTES,
        "characters/<id>.json",
    ),
    "setting_character_requirements": (
        SETTING_CHARACTER_REQUIREMENTS_SCHEMA,
        SETTING_REQUIREMENTS_EXAMPLE,
        SETTING_REQUIREMENTS_NOTES,
        "character-requirements.json",
    ),
    "ruleset_source": (
        RULESET_SOURCE_SCHEMA,
        RULESET_SOURCE_EXAMPLE,
        RULESET_SOURCE_NOTES,
        "ruleset-source.yaml",
    ),
    "panel_declaration": (
        PANEL_DECLARATION_SCHEMA,
        PANEL_DECLARATION_EXAMPLE,
        PANEL_DECLARATION_NOTES,
        "ruleset-source.yaml:ui_surfaces",
    ),
    "campaign_plan": (
        CAMPAIGN_PLAN_SCHEMA,
        CAMPAIGN_PLAN_EXAMPLE,
        CAMPAIGN_PLAN_NOTES,
        "plan.json",
    ),
    "interview_contribution": (
        INTERVIEW_CONTRIBUTION_SCHEMA,
        INTERVIEW_CONTRIBUTION_EXAMPLE,
        INTERVIEW_CONTRIBUTION_NOTES,
        "interview-contribution.json",
    ),
    "panel_layout": (
        PANEL_LAYOUT_SCHEMA,
        PANEL_LAYOUT_EXAMPLE,
        PANEL_LAYOUT_NOTES,
        "ui/panel-layout.json",
    ),
}


def list_content_definitions() -> list[dict[str, str]]:
    """Return the installed definition catalog without loading unrelated content or campaigns."""

    return [
        {"name": name, "id": schema["$id"], "title": schema["title"], "file": file}
        for name, (schema, _example, _notes, file) in _DEFINITIONS.items()
    ]


def get_content_definition(name: str) -> dict[str, Any]:
    """Return owned schema/example data plus the remaining procedural admission requirements."""

    if name not in _DEFINITIONS:
        raise ValueError("Unknown content definition")
    schema, example, notes, file = _DEFINITIONS[name]
    invalid_example = {**deepcopy(example), "unsupported_field": True}
    return {
        "name": name,
        "file": file,
        "schema": deepcopy(schema),
        "example": deepcopy(example),
        "invalid_example": invalid_example,
        "invalid_example_problem": (
            "Remove $.unsupported_field: this format accepts only its declared root fields."
        ),
        "requirements": list(notes),
        "validation_scope": (
            "Structure plus the listed procedural requirements; "
            "not publication or gameplay testing."
        ),
        **(
            {"procedural_definition": deepcopy(RUNTIME_PROCEDURAL_DEFINITION)}
            if name == "package_runtime"
            else {}
        ),
    }
