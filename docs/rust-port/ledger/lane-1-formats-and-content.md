# Lane 1 ledger: Formats and content

Python snapshot `0da1010feb5a520f917f5d69ff8553ddb174e9e7`. 141 files, 41,552 lines (thirty Builder files from lane 4, 24 Nov 2026).

One row per Python file this lane retires. Status is `pending`, `in progress`,
`ported` (Rust replacement merged and its comparison checks pass) or `retired`
(no Rust replacement needed; say why in Notes). Only this lane's worker edits this
file; a row moves to another lane only through the manager.

| Python file | Lines | Rust target | Status | Notes |
| --- | ---: | --- | --- | --- |
| `src/sylvia_gm/agent/_builder_question_answers.py` | 127 | `sylvia-agent::builder::question_answers` | ported | `builder_question_details`, `builder_question_answer_content` (vectors `builder_foundation_vectors`) |
| `src/sylvia_gm/agent/_builder_reference_copy_tool.py` | 98 | `sylvia-agent::builder` | pending | |
| `src/sylvia_gm/agent/_builder_session.py` | 2,709 | `sylvia-agent::builder` | in progress | B1: `_provider_history`, `_coalesce_builder_responses` and `_builder_native_request` (`builder::history`); `_creation_subflow_purpose`, `_effective_tracked_skill` and `_builder_prompt` (`builder::prompt`, its texts read from the Python source by `generate_builder_texts.py`); `_tool_call_details`, `_durable_tool_result` and `_builder_tool_evidence` (`builder::evidence`) (vectors `builder_foundation_vectors`). The turns follow in B3, the tools in B4 to B7. `run_builder_turn` is not ported: only Python tests call it (see "Retired files" below) |
| `src/sylvia_gm/agent/_builder_source_tool.py` | 75 | `sylvia-agent::builder` | pending | |
| `src/sylvia_gm/agent/_builder_title.py` | 64 | `sylvia-agent::builder::title` | ported | `opening_title`, `FirstReplyTitle`, `TITLE_INSTRUCTION` (exported) (vectors `builder_foundation_vectors`) |
| `src/sylvia_gm/agent/_character_draft_tool.py` | 99 | `sylvia-agent::builder` | pending | |
| `src/sylvia_gm/agent/_character_profile_tools.py` | 350 | `sylvia-agent::builder` | pending | |
| `src/sylvia_gm/agent/_character_reuse_tool.py` | 106 | `sylvia-agent::builder` | pending | |
| `src/sylvia_gm/agent/_content_definition_tool.py` | 140 | `sylvia-agent::builder` | pending | |
| `src/sylvia_gm/agent/_history.py` | 62 | - | retired | No live caller; see "Retired files" below |
| `src/sylvia_gm/agent/_import_repair_tools.py` | 73 | `sylvia-agent::builder` | pending | |
| `src/sylvia_gm/agent/_library_source_copy_tool.py` | 156 | `sylvia-agent::builder` | pending | |
| `src/sylvia_gm/agent/_library_source_tool.py` | 146 | `sylvia-agent::builder` | pending | |
| `src/sylvia_gm/agent/_mode.py` | 13 | `sylvia-agent::builder::mode` | ported | `AgentMode`; the Rust runner refuses `DmSession` (vectors `builder_foundation_vectors`) |
| `src/sylvia_gm/agent/_participant_file_tool.py` | 82 | `sylvia-agent::builder` | pending | |
| `src/sylvia_gm/agent/_plan_draft_check_tool.py` | 50 | `sylvia-agent::builder` | pending | |
| `src/sylvia_gm/agent/_ruleset_draft_check_tool.py` | 75 | `sylvia-agent::builder` | pending | |
| `src/sylvia_gm/agent/_ruleset_test_coverage.py` | 93 | `sylvia-agent::builder` | pending | |
| `src/sylvia_gm/agent/_ruleset_test_provider.py` | 153 | `sylvia-agent::builder` | pending | |
| `src/sylvia_gm/agent/_ruleset_test_runner.py` | 348 | `sylvia-agent::builder` | pending | |
| `src/sylvia_gm/agent/_ruleset_test_tool.py` | 70 | `sylvia-agent::builder` | pending | |
| `src/sylvia_gm/agent/_runner.py` | 2,549 | `sylvia-agent::builder::runner` | ported | B2b: `AgentRunner` in the Builder modes (construction, `turn_stream`, `_BuilderLoopGuard`, `AgentLoopError`, `StreamEvent`, the output-limit continuation, native continuation, the `ask_question` stop, between-iteration steering, the `WIDGET_ANSWER|` conversion, `_build_tools`, `_execute_tool`, the activity callbacks and `_builder_stream_options`), proven by `builder_runner_vectors` (paired runs with one scripted provider, a cancelled turn included). Built with `history_path=None` as the Builder builds it: no history file is read or written. B2a: `_wrap_builder_handler` and the Builder tool registration are `builder::tools::handlers::builder_workspace_tools`. Not ported, no live caller in the 0.4.1 snapshot: the `store` (`StateAPI`) and `reveal_guard` parameters and every `DM_SESSION` branch (construction refuses `DmSession` with Python's `ValueError` text), and the non-streaming `turn`/`_turn_impl`, `AgentTurnResult`, `_turn_with_overflow_recovery`, `_compress_and_retry`, `_detect_truncation` and `ContextExhaustedError`; see "Retired files" below. `SessionUsageTracker` (never read by the Builder) is not ported. |
| `src/sylvia_gm/agent/_skills.py` | 59 | `sylvia-agent::builder::skills` | ported | `builder_skills`, `builder_skill_references`, `load_builder_skill`. The skills and their references are exported into `builder/builder_texts.json` by `builder/generate_builder_texts.py` (vectors `builder_foundation_vectors`) |
| `src/sylvia_gm/application/__init__.py` | 1 | `sylvia-app::__init__` | pending | |
| `src/sylvia_gm/application/_srd_lite_character_legacy.py` | 315 | `sylvia-app::srd_lite_character_compatibility` | ported | Data only: the compressed Lite 0.2 contract is exported as `sylvia-app/src/srd_lite_legacy_character_contract.json` by `generate_srd_lite_legacy_contract.py`; read by `srd_lite_character_compatibility` |
| `src/sylvia_gm/application/_yaml_bounds.py` | 71 | `sylvia-domain::yaml_bounds` | ported | In `sylvia-domain`, not `sylvia-app`: lane 2's `package_runtime` and the Builder's Ruleset source both run it from a `construct_document` hook. `yaml_bounds_vectors` |
| `src/sylvia_gm/application/builder_references.py` | 435 | `sylvia-app::builder_references` | ported | `capture_launch_references` and `capture_content_references` (`builder_reference_vectors`, over a fake owner with real archives). The owner is `ReferenceOwner` (`LibraryOwner` plus `install_package_archive`; `AuthorityStore` implements it). Reader half: `launch_references`, `candidate_for_references`, `launch_candidate`, `with_character_examples`, `BuilderReferences` with `read`, `reader_for_references` and `reference_reader`, proven by `reference_reader_vectors` (53 cases on the Last Cart Through closure: launch contexts, including an unhashable tool name; captured and changed references; examples for both purposes; every reader path and reads of the generated files, package files and unavailable paths) |
| `src/sylvia_gm/application/builder_session_migration.py` | 136 | `sylvia-app::builder_session_migration` | ported | `builder_migration_vectors`: lane 5's legacy Builder bases and the open base with 51 stored-content changes, replayed on `AuthorityStore` and compared by result, store calls and storage tree. The owner is `MigrationOwner` (`ReferenceOwner` plus the store calls); `AuthorityStore` implements it with lane 5's `builder_launch_migration`. The catalog is the caller's (lane 5's `_campaign_creation_catalog`). |
| `src/sylvia_gm/application/builder_source_selection.py` | 123 | `sylvia-app::builder_source_selection` | pending | |
| `src/sylvia_gm/application/bundled_campaign_plans.py` | 271 | `sylvia-app::bundled_campaign_plans` | ported | `plan_catalogue_vectors` (the repository collection and synthetic collections). Methods on `BundledPackages`, whose `PlanCaches` are Python's `lru_cache`s; the collection root is `BundledSources::campaign_plans_root` |
| `src/sylvia_gm/application/bundled_packages.py` | 510 | `sylvia-app::bundled_packages` | ported | Archives, versions, Rift authority, participant declaration, interview questions and Builder sources (`bundled_vectors`); `bundled_rift_archive_for_closure` and `bundled_rift_campaign_plan` in `plan_snapshot_vectors`; `bundled_campaign_content_catalog` in `content_catalog_vectors` (the repository's content and synthetic bundled Plan collections). Python's `sys._MEIPASS` and `__file__` roots are `BundledSources` |
| `src/sylvia_gm/application/bundled_starter_characters.py` | 150 | `sylvia-app::bundled_starter_characters` | ported | `starter_vectors`: scenarios replayed on one `BundledPackages` each, compared by participants, `on_invalid` reports and the storage tree after every step. Python's process-wide `_ROSTERS` (cleared at 32 keys) and `_bundled_bindings` are `BundledPackages` caches with Python's key; the starter source is `BundledSources::srd_starters` |
| `src/sylvia_gm/application/campaign_library_selection.py` | 668 | `sylvia-app::campaign_library_selection` | ported | `participant_catalog_payload`, `_character_state_reference` and `_participant_choice` (`participant_catalog_vectors`). The selection (`selection_vectors`, over a fake owner with real admitted archives): `CampaignLibrarySelection` with `party_options`, `read_required_plan_characters`, `require_completed_plan_characters`, `resolve_campaign_plan_selection` with `_plan_party_template`, `resolve_campaign_library_selection` and `plan_with_interview_intent`. The owner is `SelectionOwner` (`ReferenceOwner` plus `exact_participant_declarations` and `export_package_candidate`; `AuthorityStore` implements it, its export answering `rust_port_pending` until lane 3's synchronous `export_package_candidate_on`). The party (`party_vectors`): `materialize_library_party` and `materialize_template_party` |
| `src/sylvia_gm/application/campaign_plan_sources.py` | 192 | `sylvia-app::campaign_plan_sources` | ported | `plan_catalogue_vectors` (over lane 3's package-read base with `AuthorityStore`). Owner reads through `library_sources::LibraryOwner`; `read_setting` is a method keeping the owner |
| `src/sylvia_gm/application/campaign_starting_world.py` | 97 | `sylvia-app::campaign_starting_world` | ported | `app_leaves_vectors` |
| `src/sylvia_gm/application/character_card_import.py` | 628 | `sylvia-app::character_card_import` | pending | |
| `src/sylvia_gm/application/character_profiles.py` | 391 | `sylvia-app::character_profiles` | ported | `character_profile_vectors` (one file for Windows and Linux); reads through `pyjson::loads_hooked`; differences rows 1, 3 and 4 |
| `src/sylvia_gm/application/character_rules.py` | 145 | `sylvia-app::character_rules` | ported | `validate_character_rules` and `campaign_character_state` (`character_rules_vectors`); `prepare_character_rules` and `verify_character_rules` run lane 2's `execute_effects` (`character_state_vectors`, on SRD Lite's own validation program) |
| `src/sylvia_gm/application/compatible_character_copy.py` | 171 | `sylvia-app::compatible_character_copy` | ported | `authored_character_state` and `copy_compatible_character` (`CopyDestination`), proven by `reference_reader_vectors` (a copy, a stale or malformed digest, no authored profile, a Setting schema requiring `home`, a changed role, no entity-state schema and a participant schema the data fails) |
| `src/sylvia_gm/application/import_repair.py` | 621 | `sylvia-app::import_repair` | pending | |
| `src/sylvia_gm/application/import_repair_sessions.py` | 147 | `sylvia-app::import_repair_sessions` | pending | |
| `src/sylvia_gm/application/initial_character_state.py` | 532 | `sylvia-app::initial_character_state` | ported | Admission (`package_library_vectors` contracts); materialize, verify, `initialize_campaign_character_state` and `verify_placed_character` (`character_state_vectors`: SRD Lite's declaration with its twelve starters, and a synthetic declaration). `_RequiredInputs` is lane 2's `EvalContext.required_inputs`; differences row 41 (merge results in creation calculations) |
| `src/sylvia_gm/application/interview_content.py` | 152 | `sylvia-app::interview_content` | ported | `resolve_interview_content` (`interview_content_vectors`: 260 cases over a fake owner that installs real archives, with and without a storage root holding a saved Rift sibling): the Ruleset-only and Setting paths, bundled and saved Plans, the bundled Rift closure and bundled Setting snapshots installed. The owner is `ReferenceOwner` (`AuthorityStore` implements it); `InterviewOwner` is gone. `require_active_ruleset` and `ARCHIVED_RULESET_IDS` (`launch_compatibility.py`) live here |
| `src/sylvia_gm/application/library_sources.py` | 223 | `sylvia-app::library_sources` | ported | `LibrarySourceReader` over the `LibraryOwner` trait (`library_source_vectors`), implemented for `AuthorityStore` with lane 3's synchronous reads |
| `src/sylvia_gm/application/network_graph.py` | 433 | `sylvia-app::network_graph` | ported | `app_leaves_vectors` |
| `src/sylvia_gm/application/package_library.py` | 271 | `sylvia-app::package_library` | ported | `package_library_vectors`: installs through a recording store (repository archives under 1 MB and two SRD-carrying ones, and archives built from the small Ruleset source), predicates, admissions, surfaces, rebinds and migrations. `PackageStorePort` is implemented for `sylvia_store::AuthorityStore` |
| `src/sylvia_gm/application/package_materialization.py` | 260 | `sylvia-app::package_materialization` | ported | `bundled_vectors` (every bundled archive byte for byte, synthetic drafts); ZIP bytes through `package_archive::zipfile_write` |
| `src/sylvia_gm/application/plan_bundle.py` | 164 | `sylvia-app::plan_bundle` | pending | |
| `src/sylvia_gm/application/plan_network_admission.py` | 109 | `sylvia-app::plan_network_admission` | ported | `plan_admission_vectors`. Uses lane 2's `resolve_surface_source` and lane 1's `network_graph` projection |
| `src/sylvia_gm/application/plan_snapshot.py` | 113 | `sylvia-app::plan_snapshot` | ported | `plan_snapshot_vectors`, with the `lru_cache` of snapshot facts. `retained_plan_snapshot` takes a `PlanSnapshotOwner`; `AuthorityStore` implements it, its export through lane 3's `export_package_candidate_on` (`snapshot_export_store`) |
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
| `src/sylvia_gm/campaign/rift.py` | 543 | `sylvia-app::campaign_rift` | ported | `rift_vectors`: template validation, the shipped template (cached per `BundledPackages`, from `BundledSources::rift_party_template`), the public setup metadata, `materialize_rift_selection` over lanes, policies, choices, templates, reusable sets and Setting schemas, and `validate_rift_party_handoff`. In `sylvia-app`, not `sylvia-domain`: it runs the bundled Rift Plan, the initial character state check and the starting world. An encounter list whose pair key is not text answers `rust_port_pending`: a JSON object cannot hold it |
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
| `src/sylvia_gm/tools/__init__.py` | 22 | `sylvia-agent::builder::tools` | ported | Re-exports only (`builder::tools`) |
| `src/sylvia_gm/tools/_builder_handlers.py` | 504 | `sylvia-agent::builder::tools::handlers` | in progress | B2a: `tool_read_file`, `tool_write_file`, `tool_edit_file`, `tool_search_files` (`Path.rglob` in Python 3.12's selector order, `builder::tools::glob`; the regex through `builder::tools::pattern`, differences row for untranslatable patterns) and `tool_delete_file` behind the runner's `_wrap_builder_handler`, and `tool_ask_question` (vectors `builder_tool_vectors`, per platform). `tool_web_search` checks its query and limit; its request answers `rust_port_pending` until B2c. `tool_create_campaign_folder` is not ported: no live caller in the 0.4.1 snapshot (`create_campaign_folder` is never registered). Builder prompt text that names it stays byte-exact. |
| `src/sylvia_gm/tools/_converter.py` | 65 | `sylvia-agent::builder::tools::spec` | ported | `tool_spec_to_openai`, with the parameter-only fallback (vectors `builder_tool_vectors`, per platform) |
| `src/sylvia_gm/tools/_dm_campaign_handlers.py` | 252 | - | retired | Serves only the runner's `DM_SESSION` branches; see "Retired files" below |
| `src/sylvia_gm/tools/_registry.py` | 254 | `sylvia-agent::builder::tools::registry` | ported | `ToolRegistry`: registration order, mode gating, `execute`'s error shapes (vectors `builder_tool_vectors`, per platform). `get_combat_tool_names` (DM only) and logging are not ported |
| `src/sylvia_gm/tools/_spec.py` | 126 | `sylvia-agent::builder::tools::spec` | ported | `ToolSpec.from_openai_schema`, `ToolParameter` as specs use it (vectors `builder_tool_vectors`, per platform) |
| `src/sylvia_gm/tools/_toolsets.py` | 55 | `sylvia-agent::builder::tools::spec` | ported | `ToolsetName`, `resolve_mode_toolsets` |

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

Retired with the Builder handover (lane 1, 24 Nov 2026): `agent/_history.py` has no live caller.
The Builder's two `AgentRunner` constructions in `agent/_builder_session.py` pass
`history_path=None` with an explicit `initial_history`, so `load_history` and `save_history` never
touch a file, and nothing else in `src/` or `scripts/` calls them (`session/_history.py` only
re-exports them). `tools/_dm_campaign_handlers.py` serves only the runner's `DM_SESSION` branches,
which the Rust runner does not port (see the `agent/_runner.py` row); lane 4 agreed this one.

Agreed with the manager on 24 Nov 2026 (B2b): the owner reaches the Builder only through
`stream_builder_turn`, which uses `AgentRunner.turn_stream` (`interfaces/commands/sessions.py`).
`run_builder_turn`, and the non-streaming `AgentRunner.turn` / `_turn_impl` it uses, are called only
from Python tests; nothing in `src/` or `scripts/` calls them, nor does SylviaBench or Trigger
Discipline's own code. `_turn_with_overflow_recovery`, `_compress_and_retry`, `_detect_truncation` and
`ContextExhaustedError` serve only `_turn_impl`'s `DM_SESSION` branch. `AgentTurnResult` has one more
user, `session/_divergence.py`'s `DivergenceHandler.handle_divergence` (re-exported by
`session/__init__.py`), which is reached only from the runner's `DM_SESSION` branches (`_divergence`
is set only in DM mode), so it is retired with them; lane 4 retired `session/_divergence.py` in this
PR with their agreement.
