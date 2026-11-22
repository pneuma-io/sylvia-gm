# Lane 1 ledger: Formats and content

Python snapshot `0da1010feb5a520f917f5d69ff8553ddb174e9e7`. 111 files, 32,577 lines.

One row per Python file this lane retires. Status is `pending`, `in progress`,
`ported` (Rust replacement merged and its comparison checks pass) or `retired`
(no Rust replacement needed; say why in Notes). Only this lane's worker edits this
file; a row moves to another lane only through the manager.

| Python file | Lines | Rust target | Status | Notes |
| --- | ---: | --- | --- | --- |
| `src/sylvia_gm/application/__init__.py` | 1 | `sylvia-app::__init__` | pending | |
| `src/sylvia_gm/application/_srd_lite_character_legacy.py` | 315 | `sylvia-app::srd_lite_character_compatibility` | ported | Data only: the compressed Lite 0.2 contract is exported as `sylvia-app/src/srd_lite_legacy_character_contract.json` by `generate_srd_lite_legacy_contract.py`; read by `srd_lite_character_compatibility` |
| `src/sylvia_gm/application/_yaml_bounds.py` | 71 | `sylvia-domain::yaml_bounds` | ported | In `sylvia-domain`, not `sylvia-app`: lane 2's `package_runtime` and the Builder's Ruleset source both run it from a `construct_document` hook. `yaml_bounds_vectors` |
| `src/sylvia_gm/application/builder_references.py` | 435 | `sylvia-app::builder_references` | pending | |
| `src/sylvia_gm/application/builder_session_migration.py` | 136 | `sylvia-app::builder_session_migration` | pending | |
| `src/sylvia_gm/application/builder_source_selection.py` | 123 | `sylvia-app::builder_source_selection` | pending | |
| `src/sylvia_gm/application/bundled_campaign_plans.py` | 271 | `sylvia-app::bundled_campaign_plans` | pending | |
| `src/sylvia_gm/application/bundled_packages.py` | 510 | `sylvia-app::bundled_packages` | in progress | Archives, versions, Rift authority, participant declaration, interview questions and Builder sources ported (`bundled_vectors`); `bundled_rift_archive_for_closure` and `bundled_rift_campaign_plan` in `plan_snapshot_vectors`. `bundled_campaign_content_catalog` waits on `bundled_campaign_plans.py` |
| `src/sylvia_gm/application/bundled_starter_characters.py` | 150 | `sylvia-app::bundled_starter_characters` | pending | |
| `src/sylvia_gm/application/campaign_library_selection.py` | 668 | `sylvia-app::campaign_library_selection` | pending | |
| `src/sylvia_gm/application/campaign_plan_sources.py` | 192 | `sylvia-app::campaign_plan_sources` | pending | |
| `src/sylvia_gm/application/campaign_starting_world.py` | 97 | `sylvia-app::campaign_starting_world` | ported | `app_leaves_vectors` |
| `src/sylvia_gm/application/character_card_import.py` | 628 | `sylvia-app::character_card_import` | pending | |
| `src/sylvia_gm/application/character_profiles.py` | 391 | `sylvia-app::character_profiles` | ported | `character_profile_vectors` (one file for Windows and Linux); reads through `pyjson::loads_hooked`; differences rows 1, 3 and 4 |
| `src/sylvia_gm/application/character_rules.py` | 145 | `sylvia-app::character_rules` | ported | `validate_character_rules` and `campaign_character_state` (`character_rules_vectors`); `prepare_character_rules` and `verify_character_rules` run lane 2's `execute_effects` (`character_state_vectors`, on SRD Lite's own validation program) |
| `src/sylvia_gm/application/compatible_character_copy.py` | 171 | `sylvia-app::compatible_character_copy` | pending | |
| `src/sylvia_gm/application/import_repair.py` | 621 | `sylvia-app::import_repair` | pending | |
| `src/sylvia_gm/application/import_repair_sessions.py` | 147 | `sylvia-app::import_repair_sessions` | pending | |
| `src/sylvia_gm/application/initial_character_state.py` | 532 | `sylvia-app::initial_character_state` | ported | Admission (`package_library_vectors` contracts); materialize, verify, `initialize_campaign_character_state` and `verify_placed_character` (`character_state_vectors`: SRD Lite's declaration with its twelve starters, and a synthetic declaration). `_RequiredInputs` is lane 2's `EvalContext.required_inputs`; differences row 41 (merge results in creation calculations) |
| `src/sylvia_gm/application/interview_content.py` | 152 | `sylvia-app::interview_content` | in progress | `resolve_interview_content`: the Ruleset-only and installed-Setting paths are exact (`interview_content_vectors`). These branches answer `rust_port_pending`: a Plan selection stops at `bundled_campaign_plans.bundled_plan` (waits on `plan_snapshot`, that is lane 2's `tactical_grid`); installing the bundled Rift closure stops at `install_package_archive` (waits on lane 2's `package_runtime`); a bundled Setting stops at `bundled_campaign_plans.bundled_setting_snapshot` (waits on `plan_snapshot`). The owner reads are the `InterviewOwner` trait, for lane 3's store to implement |
| `src/sylvia_gm/application/library_sources.py` | 223 | `sylvia-app::library_sources` | ported | `LibrarySourceReader` over the `LibraryOwner` trait (`library_source_vectors`), implemented for `AuthorityStore` with lane 3's synchronous reads |
| `src/sylvia_gm/application/network_graph.py` | 433 | `sylvia-app::network_graph` | ported | `app_leaves_vectors` |
| `src/sylvia_gm/application/package_library.py` | 271 | `sylvia-app::package_library` | ported | `package_library_vectors`: installs through a recording store (repository archives under 1 MB and two SRD-carrying ones, and archives built from the small Ruleset source), predicates, admissions, surfaces, rebinds and migrations. `PackageStorePort` is implemented for `sylvia_store::AuthorityStore` |
| `src/sylvia_gm/application/package_materialization.py` | 260 | `sylvia-app::package_materialization` | ported | `bundled_vectors` (every bundled archive byte for byte, synthetic drafts); ZIP bytes through `package_archive::zipfile_write` |
| `src/sylvia_gm/application/plan_bundle.py` | 164 | `sylvia-app::plan_bundle` | pending | |
| `src/sylvia_gm/application/plan_network_admission.py` | 109 | `sylvia-app::plan_network_admission` | ported | `plan_admission_vectors`. Uses lane 2's `resolve_surface_source` and lane 1's `network_graph` projection |
| `src/sylvia_gm/application/plan_snapshot.py` | 113 | `sylvia-app::plan_snapshot` | in progress | `plan_snapshot_vectors`, with the `lru_cache` of snapshot facts. `retained_plan_snapshot` takes a `PlanSnapshotOwner`; its `AuthorityStore` impl waits on lane 3's `export_package_candidate` |
| `src/sylvia_gm/application/plan_spatial_admission.py` | 161 | `sylvia-app::plan_spatial_admission` | ported | `plan_admission_vectors`. Uses lane 2's `resolve_surface_source` and `admit_spatial_scene`; `_plan_state` is `plan_state_admission::plan_state` |
| `src/sylvia_gm/application/plan_state_admission.py` | 124 | `sylvia-app::plan_state_admission` | ported | `plan_state_vectors`. Also holds `plan_spatial_admission._plan_state`, which it reads |
| `src/sylvia_gm/application/ready_campaigns.py` | 504 | `sylvia-app::ready_campaigns` | pending | |
| `src/sylvia_gm/application/reference_text.py` | 93 | `sylvia-app::reference_text` | pending | |
| `src/sylvia_gm/application/reusable_library_deletion.py` | 88 | `sylvia-app::reusable_library_deletion` | ported | `character_profile_vectors` (storage scripts: links, tampering, delete and retry) |
| `src/sylvia_gm/application/reusable_participants.py` | 799 | `sylvia-app::reusable_participants` | ported | `reusable_participant_vectors`: Character File parses on the current and 0.3.0 SRD Lite declarations (including the retained-contract weapon-set migration), and a replayed save/load/list/delete scenario with SRD Lite's twelve starters. `ParticipantValidationError` is the new `DomainError::Issues`; a Character File with `NaN` is refused as invalid JSON (row 2) |
| `src/sylvia_gm/application/reusable_plans.py` | 575 | `sylvia-app::reusable_plans` | ported | `reusable_plan_vectors` (a 430-step scenario replayed from Python). Validators are closures; the Rift fallback reads the caller's `BundledPackages`. Uses `pyjson::loads_bytes`, `pyre` templates, `pystr::splitlines` and `pystr::isidentifier` (`pytext_vectors`) |
| `src/sylvia_gm/application/ruleset_testing.py` | 111 | `sylvia-app::ruleset_testing` | pending | |
| `src/sylvia_gm/application/schema_admission.py` | 741 | `sylvia-domain::schema_admission` | ported | In `sylvia-domain`, not `sylvia-app`: package archive admission (this crate) and the engine, store and agent lanes call it |
| `src/sylvia_gm/application/setting_character_requirements.py` | 198 | `sylvia-app::setting_character_requirements` | ported | `app_leaves_vectors` |
| `src/sylvia_gm/application/srd_lite_character_compatibility.py` | 61 | `sylvia-app::srd_lite_character_compatibility` | ported | `srd_lite_compat_vectors` (the bundled player declaration stands in for the lost 0.3.0 one) |
| `src/sylvia_gm/application/srd_lite_weapon_set_migration.py` | 58 | `sylvia-app::srd_lite_weapon_set_migration` | ported | `app_leaves_vectors` |
| `src/sylvia_gm/builder/__init__.py` | 46 | - | ported | Re-exports only: `PathGuardrail` and `GuardrailBlockedError` are `sylvia-app::path_guardrail`, `build_builder_system_prompt` is `sylvia-app::builder_prompts`; `BuilderLoopError`, `BuilderRunner` and `BuilderTurnResult` are lazy re-exports of lane 4's `agent/_runner.py`; the interview extractor re-exports are dropped with the retired files |
| `src/sylvia_gm/builder/_campaign_plan_materializer.py` | 149 | `sylvia-app::campaign_plan_materializer` | ported | `campaign_plan_materializer_vectors` (every shipped Plan, materialized and published, and targeted drafts; one file for Windows and Linux); differences row 2 |
| `src/sylvia_gm/builder/_guardrail.py` | 123 | `sylvia-app::path_guardrail` | ported | `guardrail_vectors`, one file per platform (`Path.resolve` differs); Windows and Linux vectors both checked |
| `src/sylvia_gm/builder/_interview_extractor.py` | 347 | - | retired | No live caller in the 0.4.1 snapshot; see "Retired files" below |
| `src/sylvia_gm/builder/_interview_manifest.py` | 57 | - | retired | No live caller in the 0.4.1 snapshot; see "Retired files" below |
| `src/sylvia_gm/builder/_package_materializer.py` | 341 | `sylvia-app::package_materializer` | ported | `package_materializer_vectors` (one file for Windows and Linux: compact sources, descriptor drafts, links, limits, export) |
| `src/sylvia_gm/builder/_package_record_edits.py` | 226 | `sylvia-app::package_record_edits` | ported | `package_record_edit_vectors` (one file for Windows and Linux) |
| `src/sylvia_gm/builder/_package_snapshots.py` | 222 | `sylvia-app::builder` | pending | |
| `src/sylvia_gm/builder/_plan_draft_check.py` | 101 | `sylvia-app::builder` | pending | |
| `src/sylvia_gm/builder/_plan_snapshot_materializer.py` | 292 | `sylvia-app::builder` | pending | |
| `src/sylvia_gm/builder/_prompts.py` | 248 | `sylvia-app::builder_prompts` | ported | `builder_prompt_vectors`. The model-read text is `builder_prompt_text.rs`, generated from the Python source by `generate_builder_prompt_text.py` |
| `src/sylvia_gm/builder/_ruleset_source.py` | 689 | `sylvia-app::ruleset_source` | ported | `ruleset_source_vectors` (the shipped Riichi source, edits, and YAML whose aliases reach the dumped runtime as anchors); Python's object graph and `deepcopy` sharing are kept for `yaml.safe_dump` |
| `src/sylvia_gm/builder/_setting_draft_check.py` | 46 | `sylvia-app::builder` | pending | |
| `src/sylvia_gm/builder/_setting_materializer.py` | 150 | `sylvia-app::builder` | pending | |
| `src/sylvia_gm/builder/_tools.py` | 496 | `sylvia-app::builder_tools` | ported | Data: `builder_tools.json`, exported from the Python values by `generate_builder_tools.py` (model-read text copied, not retyped); unit test pins the nine definitions and their tool names |
| `src/sylvia_gm/builder/_workspace_files.py` | 114 | `sylvia-app::workspace_files` | ported | Unit test: stale versions, unique and overlapping literals, atomic replace with no leftover temporary file |
| `src/sylvia_gm/campaign/__init__.py` | 10 | - | retired | No live caller in the 0.4.1 snapshot; see "Retired files" below |
| `src/sylvia_gm/campaign/_folder.py` | 600 | - | retired | No live caller in the 0.4.1 snapshot; see "Retired files" below |
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
| `src/sylvia_gm/domain/_runtime_executable_reference.py` | 225 | `sylvia-domain::content::catalog` | ported | Data only, and read only by `_content_schema_runtime.py` and `_content_schema_ruleset.py`: exported with them as `content/definitions/package_runtime.procedural.json` by `content/generate_definitions.py` |
| `src/sylvia_gm/domain/authority.py` | 1,128 | `sylvia-domain::authority` | ported | Validated types keep private fields behind `new`; `Mechanic` and `NarrationStream` (callables) left to lanes 2 and 4 |
| `src/sylvia_gm/domain/campaign_ending.py` | 99 | `sylvia-domain::campaign_ending` | ported | `plan_reading_vectors` (every shipped Campaign Plan) |
| `src/sylvia_gm/domain/campaign_plan.py` | 759 | `sylvia-domain::campaign_plan` | ported | `campaign_plan_vectors` (every shipped Campaign Plan) |
| `src/sylvia_gm/domain/campaign_sequel.py` | 123 | `sylvia-domain::campaign_sequel` | ported | `domain_records_vectors` |
| `src/sylvia_gm/domain/content_definitions.py` | 313 | `sylvia-domain::content::catalog` | ported | `list_content_definitions`, `get_content_definition` |
| `src/sylvia_gm/domain/content_validation.py` | 143 | `sylvia-domain::content::validation` | ported | `validate_content_shape`, `ContentShapeError`; the engine (`content::schema`) also serves lanes 2 and 4 and `state/_schema.py`, with jsonschema 4.26 messages |
| `src/sylvia_gm/domain/errors.py` | 188 | `sylvia-domain::error` | ported | `RulesetDiagnostic`, `MechanicsValidationIssue`; the `AuthorityError` classes are `DomainError::Rejected` with their codes. 0.4.1 follow-up: `RulesetPreconditionRejected` fills `field_paths` from `_NAMED_INPUT` (`error::named_inputs`, Python's `\b` on `pystr::is_alnum`; `schema_admission_vectors`, every code point checked) |
| `src/sylvia_gm/domain/interview_contribution.py` | 150 | `sylvia-domain::interview_contribution` | ported | `domain_records_vectors` |
| `src/sylvia_gm/domain/inventory.py` | 240 | `sylvia-domain::inventory` | ported | Shared schemas as data (`generate_inventory_schema.py`); `validate_holdings`, `transfer_holding` and `player_inventory` in `inventory/holdings.rs`; `domain_records_vectors` |
| `src/sylvia_gm/domain/mechanics_choices.py` | 89 | `sylvia-domain::mechanics_choices` | ported | `domain_records_vectors` |
| `src/sylvia_gm/domain/model_capability.py` | 333 | `sylvia-domain::model_capability` | ported | Tiers, placements, registry, guidance decisions and payloads |
| `src/sylvia_gm/domain/no_ruleset_plan.py` | 124 | `sylvia-domain::no_ruleset_plan` | ported | `plan_reading_vectors` (synthetic No Ruleset worlds on shipped Plans) |
| `src/sylvia_gm/domain/package_library.py` | 2,211 | `sylvia-domain::package_library` | ported | Manifests, declarations, gameplay support, setting knowledge and closures; Python operator quirks via `pyops` |
| `src/sylvia_gm/domain/panel_layout.py` | 105 | `sylvia-domain::panel_layout` | ported | `panel_layout_vectors` (synthetic layouts: the only shipped ones are private) and `archive_vectors` |
| `src/sylvia_gm/domain/provider_records.py` | 17 | `sylvia-domain::provider_records` | ported | A record type; `Debug` leaves out `payload` as Python's `repr` does |
| `src/sylvia_gm/domain/ruleset_tests.py` | 95 | `sylvia-domain::ruleset_tests` | ported | `domain_records_vectors`; nesting of 128 levels or more under differences row 4 |
| `src/sylvia_gm/domain/session.py` | 499 | `sylvia-domain::session` | ported | Validated records keep private fields behind `new` (or `validated()` for the evidence records) |
| `src/sylvia_gm/domain/starting_state.py` | 89 | `sylvia-domain::starting_state` | ported |  |
| `src/sylvia_gm/domain/state_ownership.py` | 23 | `sylvia-domain::state_ownership` | ported | Replaces lane 3's marked private copy |
| `src/sylvia_gm/infrastructure/package_archive.py` | 365 | `sylvia-domain::package_archive` | ported | Moved from lane 3 on 21 Nov 2026: pure archive reading and admission, no database. CPython's `zipfile` read path is ported in `package_archive/zipfile.rs`. `archive_vectors`. Lane 3's #370 (reviewed by lane 1) adds the `ZipSource` positioned-read seam (a byte slice, or a `File` through std's `FileExt`, so archives stream from disk as CPython reads them) and five central-directory fields plus the archive comment, proven by `zip_info_vectors` and slice-versus-file agreement over 201 archives |
| `src/sylvia_gm/infrastructure/zip_bounds.py` | 97 | `sylvia-domain::package_archive` | ported | Moved from lane 3 on 21 Nov 2026: pure archive reading and admission, no database. `package_archive/zip_bounds.rs`; `archive_vectors` |
| `src/sylvia_gm/planning/__init__.py` | 52 | - | retired | No live caller in the 0.4.1 snapshot; see "Retired files" below |
| `src/sylvia_gm/planning/_campaign_bible.py` | 178 | - | retired | No live caller in the 0.4.1 snapshot; see "Retired files" below |
| `src/sylvia_gm/planning/_planning_output.py` | 71 | - | retired | No live caller in the 0.4.1 snapshot; see "Retired files" below |
| `src/sylvia_gm/planning/_reveal_registry.py` | 94 | - | retired | No live caller in the 0.4.1 snapshot; see "Retired files" below |
| `src/sylvia_gm/state/__init__.py` | 70 | - | retired | No live caller in the 0.4.1 snapshot; see "Retired files" below |
| `src/sylvia_gm/state/_changelog.py` | 373 | - | retired | No live caller in the 0.4.1 snapshot; see "Retired files" below |
| `src/sylvia_gm/state/_exceptions.py` | 144 | - | retired | No live caller in the 0.4.1 snapshot; see "Retired files" below |
| `src/sylvia_gm/state/_migrate.py` | 140 | - | retired | No live caller in the 0.4.1 snapshot; see "Retired files" below |
| `src/sylvia_gm/state/_pre_init.py` | 209 | - | retired | No live caller in the 0.4.1 snapshot; see "Retired files" below |
| `src/sylvia_gm/state/_ruleset_config.py` | 131 | - | retired | No live caller in the 0.4.1 snapshot; see "Retired files" below |
| `src/sylvia_gm/state/_schema.py` | 113 | - | retired | No live caller in the 0.4.1 snapshot; see "Retired files" below |
| `src/sylvia_gm/state/_snapshot.py` | 136 | - | retired | No live caller in the 0.4.1 snapshot; see "Retired files" below |
| `src/sylvia_gm/state/_store.py` | 290 | - | retired | No live caller in the 0.4.1 snapshot; see "Retired files" below |
| `src/sylvia_gm/state/_validators.py` | 316 | - | retired | No live caller in the 0.4.1 snapshot; see "Retired files" below |
| `src/sylvia_gm/state/state_api.py` | 2,359 | - | retired | No live caller in the 0.4.1 snapshot; see "Retired files" below |

## Retired files

Agreed with the manager on 21 Nov 2026: `state/` (the legacy file-backed `StateAPI`), `planning/`
and `campaign/_folder.py` with `campaign/__init__.py` have no live caller in the 0.4.1 snapshot
(0da1010). Nothing outside `state/` constructs a `StateAPI` (in `src/`, `scripts/`, `tests/`,
SylviaBench or Trigger Discipline); the remaining references are type annotations in
`agent/_runner.py`, `session/_context.py` and `session/_npc_dialogue.py`, and `runtime`'s
`StateBridge` is built only when a `StateAPI` is passed, which never happens. `planning/` is
read only by `session/_reveal_guard.py`, whose `RevealGuard` is never constructed.
`campaign/_folder.py` is reached only from `tools/_builder_handlers.py`'s
`tool_create_campaign_folder`, which is never registered (`tests/test_alpha_f6f_builder_executor.py`
asserts it is not). Lane 4 drops the type-only references when it ports those files. The
Builder prompt text that names `create_campaign_folder` (`builder/_prompts.py`) is model-read
and stays byte for byte. `campaign/rift.py` is live and is ported.
`builder/_interview_extractor.py` and `builder/_interview_manifest.py` are retired the same way (agreed
21 Nov 2026): they reference only each other and `builder/__init__.py`'s re-export, and nothing in
`src/`, `scripts/`, `tests/`, SylviaBench or Trigger Discipline uses `InterviewExtractor`,
`InterviewManifest` or `ExtractionError`. The `builder/__init__.py` port drops the two re-exports.
