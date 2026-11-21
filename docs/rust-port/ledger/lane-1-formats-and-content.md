# Lane 1 ledger: Formats and content

Python snapshot `0da1010feb5a520f917f5d69ff8553ddb174e9e7`. 111 files, 32,577 lines.

One row per Python file this lane retires. Status is `pending`, `in progress`,
`ported` (Rust replacement merged and its comparison checks pass) or `retired`
(no Rust replacement needed; say why in Notes). Only this lane's worker edits this
file; a row moves to another lane only through the manager.

| Python file | Lines | Rust target | Status | Notes |
| --- | ---: | --- | --- | --- |
| `src/sylvia_gm/application/__init__.py` | 1 | `sylvia-app::__init__` | pending | |
| `src/sylvia_gm/application/_srd_lite_character_legacy.py` | 315 | `sylvia-app::_srd_lite_character_legacy` | pending | |
| `src/sylvia_gm/application/_yaml_bounds.py` | 71 | `sylvia-app::_yaml_bounds` | pending | |
| `src/sylvia_gm/application/builder_references.py` | 435 | `sylvia-app::builder_references` | pending | |
| `src/sylvia_gm/application/builder_session_migration.py` | 136 | `sylvia-app::builder_session_migration` | pending | |
| `src/sylvia_gm/application/builder_source_selection.py` | 123 | `sylvia-app::builder_source_selection` | pending | |
| `src/sylvia_gm/application/bundled_campaign_plans.py` | 271 | `sylvia-app::bundled_campaign_plans` | pending | |
| `src/sylvia_gm/application/bundled_packages.py` | 510 | `sylvia-app::bundled_packages` | pending | |
| `src/sylvia_gm/application/bundled_starter_characters.py` | 150 | `sylvia-app::bundled_starter_characters` | pending | |
| `src/sylvia_gm/application/campaign_library_selection.py` | 668 | `sylvia-app::campaign_library_selection` | pending | |
| `src/sylvia_gm/application/campaign_plan_sources.py` | 192 | `sylvia-app::campaign_plan_sources` | pending | |
| `src/sylvia_gm/application/campaign_starting_world.py` | 97 | `sylvia-app::campaign_starting_world` | pending | |
| `src/sylvia_gm/application/character_card_import.py` | 628 | `sylvia-app::character_card_import` | pending | |
| `src/sylvia_gm/application/character_profiles.py` | 391 | `sylvia-app::character_profiles` | pending | |
| `src/sylvia_gm/application/character_rules.py` | 145 | `sylvia-app::character_rules` | pending | |
| `src/sylvia_gm/application/compatible_character_copy.py` | 171 | `sylvia-app::compatible_character_copy` | pending | |
| `src/sylvia_gm/application/import_repair.py` | 621 | `sylvia-app::import_repair` | pending | |
| `src/sylvia_gm/application/import_repair_sessions.py` | 147 | `sylvia-app::import_repair_sessions` | pending | |
| `src/sylvia_gm/application/initial_character_state.py` | 532 | `sylvia-app::initial_character_state` | pending | |
| `src/sylvia_gm/application/interview_content.py` | 152 | `sylvia-app::interview_content` | pending | |
| `src/sylvia_gm/application/library_sources.py` | 223 | `sylvia-app::library_sources` | pending | |
| `src/sylvia_gm/application/network_graph.py` | 433 | `sylvia-app::network_graph` | pending | |
| `src/sylvia_gm/application/package_library.py` | 271 | `sylvia-app::package_library` | pending | |
| `src/sylvia_gm/application/package_materialization.py` | 260 | `sylvia-app::package_materialization` | pending | |
| `src/sylvia_gm/application/plan_bundle.py` | 164 | `sylvia-app::plan_bundle` | pending | |
| `src/sylvia_gm/application/plan_network_admission.py` | 109 | `sylvia-app::plan_network_admission` | pending | |
| `src/sylvia_gm/application/plan_snapshot.py` | 113 | `sylvia-app::plan_snapshot` | pending | |
| `src/sylvia_gm/application/plan_spatial_admission.py` | 161 | `sylvia-app::plan_spatial_admission` | pending | |
| `src/sylvia_gm/application/plan_state_admission.py` | 124 | `sylvia-app::plan_state_admission` | pending | |
| `src/sylvia_gm/application/ready_campaigns.py` | 504 | `sylvia-app::ready_campaigns` | pending | |
| `src/sylvia_gm/application/reference_text.py` | 93 | `sylvia-app::reference_text` | pending | |
| `src/sylvia_gm/application/reusable_library_deletion.py` | 88 | `sylvia-app::reusable_library_deletion` | pending | |
| `src/sylvia_gm/application/reusable_participants.py` | 799 | `sylvia-app::reusable_participants` | pending | |
| `src/sylvia_gm/application/reusable_plans.py` | 575 | `sylvia-app::reusable_plans` | pending | |
| `src/sylvia_gm/application/ruleset_testing.py` | 111 | `sylvia-app::ruleset_testing` | pending | |
| `src/sylvia_gm/application/schema_admission.py` | 741 | `sylvia-domain::schema_admission` | ported | In `sylvia-domain`, not `sylvia-app`: package archive admission (this crate) and the engine, store and agent lanes call it |
| `src/sylvia_gm/application/setting_character_requirements.py` | 198 | `sylvia-app::setting_character_requirements` | pending | |
| `src/sylvia_gm/application/srd_lite_character_compatibility.py` | 61 | `sylvia-app::srd_lite_character_compatibility` | pending | |
| `src/sylvia_gm/application/srd_lite_weapon_set_migration.py` | 58 | `sylvia-app::srd_lite_weapon_set_migration` | pending | |
| `src/sylvia_gm/builder/__init__.py` | 46 | `sylvia-app::builder` | pending | |
| `src/sylvia_gm/builder/_campaign_plan_materializer.py` | 149 | `sylvia-app::builder` | pending | |
| `src/sylvia_gm/builder/_guardrail.py` | 123 | `sylvia-app::builder` | pending | |
| `src/sylvia_gm/builder/_interview_extractor.py` | 347 | `sylvia-app::builder` | pending | |
| `src/sylvia_gm/builder/_interview_manifest.py` | 57 | `sylvia-app::builder` | pending | |
| `src/sylvia_gm/builder/_package_materializer.py` | 341 | `sylvia-app::builder` | pending | |
| `src/sylvia_gm/builder/_package_record_edits.py` | 226 | `sylvia-app::builder` | pending | |
| `src/sylvia_gm/builder/_package_snapshots.py` | 222 | `sylvia-app::builder` | pending | |
| `src/sylvia_gm/builder/_plan_draft_check.py` | 101 | `sylvia-app::builder` | pending | |
| `src/sylvia_gm/builder/_plan_snapshot_materializer.py` | 292 | `sylvia-app::builder` | pending | |
| `src/sylvia_gm/builder/_prompts.py` | 248 | `sylvia-app::builder` | pending | |
| `src/sylvia_gm/builder/_ruleset_source.py` | 689 | `sylvia-app::builder` | pending | |
| `src/sylvia_gm/builder/_setting_draft_check.py` | 46 | `sylvia-app::builder` | pending | |
| `src/sylvia_gm/builder/_setting_materializer.py` | 150 | `sylvia-app::builder` | pending | |
| `src/sylvia_gm/builder/_tools.py` | 496 | `sylvia-app::builder` | pending | |
| `src/sylvia_gm/builder/_workspace_files.py` | 114 | `sylvia-app::builder` | pending | |
| `src/sylvia_gm/campaign/__init__.py` | 10 | `sylvia-domain` | pending | |
| `src/sylvia_gm/campaign/_folder.py` | 600 | `sylvia-domain` | pending | |
| `src/sylvia_gm/campaign/rift.py` | 543 | `sylvia-domain` | pending | |
| `src/sylvia_gm/domain/__init__.py` | 1 | `sylvia-domain` | pending | |
| `src/sylvia_gm/domain/_content_schema_campaign_export.py` | 276 | `sylvia-domain::content::catalog` | ported | Exported as data: `content/definitions/*.json` by `content/generate_definitions.py` |
| `src/sylvia_gm/domain/_content_schema_character.py` | 175 | `sylvia-domain::content::catalog` | ported | Exported as data: `content/definitions/*.json` by `content/generate_definitions.py` |
| `src/sylvia_gm/domain/_content_schema_character_profile.py` | 43 | `sylvia-domain::content::catalog` | ported | Exported as data: `content/definitions/*.json` by `content/generate_definitions.py` |
| `src/sylvia_gm/domain/_content_schema_character_profile_v2.py` | 252 | `sylvia-domain::content::catalog` | ported | Exported as data: `content/definitions/*.json` by `content/generate_definitions.py` |
| `src/sylvia_gm/domain/_content_schema_declarations.py` | 288 | `sylvia-domain::content::catalog` | ported | Exported as data: `content/definitions/*.json` by `content/generate_definitions.py` |
| `src/sylvia_gm/domain/_content_schema_map_card.py` | 803 | `sylvia-domain::content::catalog` | ported | Exported as data: `content/definitions/*.json` by `content/generate_definitions.py` |
| `src/sylvia_gm/domain/_content_schema_package.py` | 404 | `sylvia-domain::content::catalog` | ported | Exported as data: `content/definitions/*.json` by `content/generate_definitions.py` |
| `src/sylvia_gm/domain/_content_schema_package_subformats.py` | 258 | `sylvia-domain::content::catalog` | ported | Exported as data: `content/definitions/*.json` by `content/generate_definitions.py` |
| `src/sylvia_gm/domain/_content_schema_panel.py` | 294 | `sylvia-domain::content::catalog` | ported | Exported as data: `content/definitions/*.json` by `content/generate_definitions.py` |
| `src/sylvia_gm/domain/_content_schema_plan.py` | 841 | `sylvia-domain::content::catalog` | ported | Exported as data: `content/definitions/*.json` by `content/generate_definitions.py` |
| `src/sylvia_gm/domain/_content_schema_ruleset.py` | 445 | `sylvia-domain::content::catalog` | ported | Exported as data: `content/definitions/*.json` by `content/generate_definitions.py` |
| `src/sylvia_gm/domain/_content_schema_ruleset_tests.py` | 188 | `sylvia-domain::content::catalog` | ported | Exported as data: `content/definitions/*.json` by `content/generate_definitions.py`; `RULESET_TESTS_PATH` is `content::RULESET_TESTS_PATH` |
| `src/sylvia_gm/domain/_content_schema_runtime.py` | 320 | `sylvia-domain::content::catalog` | ported | Exported as data: `content/definitions/*.json` by `content/generate_definitions.py` |
| `src/sylvia_gm/domain/_content_schema_setting.py` | 62 | `sylvia-domain::content::catalog` | ported | Exported as data: `content/definitions/*.json` by `content/generate_definitions.py` |
| `src/sylvia_gm/domain/_content_schema_ui.py` | 209 | `sylvia-domain::content::catalog` | ported | Exported as data: `content/definitions/*.json` by `content/generate_definitions.py` |
| `src/sylvia_gm/domain/_runtime_executable_reference.py` | 225 | `sylvia-domain` | pending | |
| `src/sylvia_gm/domain/authority.py` | 1,128 | `sylvia-domain::authority` | ported | Validated types keep private fields behind `new`; `Mechanic` and `NarrationStream` (callables) left to lanes 2 and 4 |
| `src/sylvia_gm/domain/campaign_ending.py` | 99 | `sylvia-domain::campaign_ending` | ported | `plan_reading_vectors` (every shipped Campaign Plan) |
| `src/sylvia_gm/domain/campaign_plan.py` | 759 | `sylvia-domain` | pending | |
| `src/sylvia_gm/domain/campaign_sequel.py` | 123 | `sylvia-domain` | pending | |
| `src/sylvia_gm/domain/content_definitions.py` | 313 | `sylvia-domain::content::catalog` | ported | `list_content_definitions`, `get_content_definition` |
| `src/sylvia_gm/domain/content_validation.py` | 143 | `sylvia-domain::content::validation` | ported | `validate_content_shape`, `ContentShapeError`; the engine (`content::schema`) also serves lanes 2 and 4 and `state/_schema.py`, with jsonschema 4.26 messages |
| `src/sylvia_gm/domain/errors.py` | 173 | `sylvia-domain::error` | ported | `RulesetDiagnostic`, `MechanicsValidationIssue`; the `AuthorityError` classes are `DomainError::Rejected` with their codes |
| `src/sylvia_gm/domain/interview_contribution.py` | 150 | `sylvia-domain` | pending | |
| `src/sylvia_gm/domain/inventory.py` | 240 | `sylvia-domain::inventory` | in progress | Shared schemas done (data, `generate_inventory_schema.py`); `validate_holdings`, `transfer_holding` and `player_inventory` follow |
| `src/sylvia_gm/domain/mechanics_choices.py` | 89 | `sylvia-domain` | pending | |
| `src/sylvia_gm/domain/model_capability.py` | 333 | `sylvia-domain::model_capability` | ported | Tiers, placements, registry, guidance decisions and payloads |
| `src/sylvia_gm/domain/no_ruleset_plan.py` | 124 | `sylvia-domain::no_ruleset_plan` | ported | `plan_reading_vectors` (synthetic No Ruleset worlds on shipped Plans) |
| `src/sylvia_gm/domain/package_library.py` | 2,211 | `sylvia-domain::package_library` | ported | Manifests, declarations, gameplay support, setting knowledge and closures; Python operator quirks via `pyops` |
| `src/sylvia_gm/domain/panel_layout.py` | 105 | `sylvia-domain::panel_layout` | ported | `panel_layout_vectors` (synthetic layouts: the only shipped ones are private) and `archive_vectors` |
| `src/sylvia_gm/domain/provider_records.py` | 17 | `sylvia-domain` | pending | |
| `src/sylvia_gm/domain/ruleset_tests.py` | 95 | `sylvia-domain` | pending | |
| `src/sylvia_gm/domain/session.py` | 499 | `sylvia-domain::session` | ported | Validated records keep private fields behind `new` (or `validated()` for the evidence records) |
| `src/sylvia_gm/domain/starting_state.py` | 89 | `sylvia-domain::starting_state` | ported |  |
| `src/sylvia_gm/domain/state_ownership.py` | 23 | `sylvia-domain::state_ownership` | ported | Replaces lane 3's marked private copy |
| `src/sylvia_gm/infrastructure/package_archive.py` | 365 | `sylvia-domain::package_archive` | ported | Moved from lane 3 on 21 Nov 2026: pure archive reading and admission, no database. CPython's `zipfile` read path is ported in `package_archive/zipfile.rs`. `archive_vectors` |
| `src/sylvia_gm/infrastructure/zip_bounds.py` | 97 | `sylvia-domain::package_archive` | ported | Moved from lane 3 on 21 Nov 2026: pure archive reading and admission, no database. `package_archive/zip_bounds.rs`; `archive_vectors` |
| `src/sylvia_gm/planning/__init__.py` | 52 | `sylvia-domain` | pending | |
| `src/sylvia_gm/planning/_campaign_bible.py` | 178 | `sylvia-domain` | pending | |
| `src/sylvia_gm/planning/_planning_output.py` | 71 | `sylvia-domain` | pending | |
| `src/sylvia_gm/planning/_reveal_registry.py` | 94 | `sylvia-domain` | pending | |
| `src/sylvia_gm/state/__init__.py` | 70 | `sylvia-domain` | pending | |
| `src/sylvia_gm/state/_changelog.py` | 373 | `sylvia-domain` | pending | |
| `src/sylvia_gm/state/_exceptions.py` | 144 | `sylvia-domain` | pending | |
| `src/sylvia_gm/state/_migrate.py` | 140 | `sylvia-domain` | pending | |
| `src/sylvia_gm/state/_pre_init.py` | 209 | `sylvia-domain` | pending | |
| `src/sylvia_gm/state/_ruleset_config.py` | 131 | `sylvia-domain` | pending | |
| `src/sylvia_gm/state/_schema.py` | 113 | `sylvia-domain` | pending | |
| `src/sylvia_gm/state/_snapshot.py` | 136 | `sylvia-domain` | pending | |
| `src/sylvia_gm/state/_store.py` | 290 | `sylvia-domain` | pending | |
| `src/sylvia_gm/state/_validators.py` | 316 | `sylvia-domain` | pending | |
| `src/sylvia_gm/state/state_api.py` | 2,359 | `sylvia-domain` | pending | |
