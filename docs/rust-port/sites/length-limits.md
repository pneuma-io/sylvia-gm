# Length limits

Python snapshot `0da1010feb5a520f917f5d69ff8553ddb174e9e7`. 729 sites. `len()` compared with a limit. `len(str)` counts code points; Rust's `str::len` counts UTF-8 bytes. Use `chars().count()` wherever Python measured a `str`, and keep `len()` only where Python measured `bytes`.

Written by `docs/rust-port/tools/site_lists.py`. A worker ticks the Done column when the Rust side reproduces the site and a vector or comparison session proves it.

| Lane | Sites |
| --- | ---: |
| 1. Formats and content | 214 |
| 2. Game engine | 166 |
| 3. Storage and authority | 90 |
| 4. Providers and agents | 148 |
| 5. Owner, clients and release | 111 |

## Lane 1: Formats and content

| Where | Function | Kind | Code | Done |
| --- | --- | --- | --- | --- |
| `application/bundled_campaign_plans.py:116` | `read_plan_catalogue` | len() against a limit | `if not isinstance(rows, list) or not 1 <= len(rows) <= 256:` | |
| `application/bundled_starter_characters.py:117` | `bundled_starter_participants` | len() against a limit | `if len(_ROSTERS) >= 32:` | |
| `application/campaign_library_selection.py:640` | `plan_with_interview_intent` | len() against a limit | `if not isinstance(value, str) or len(value) > 131_072:` | |
| `application/character_card_import.py:128` | `_png_json` | len() against a limit | `if len(encoded) > 350000:` | |
| `application/character_card_import.py:154` | `_asset_uri` | len() against a limit | `if len(uri) > 4096 or "\x00" in uri or any(c.isspace() for c in uri):` | |
| `application/character_card_import.py:193` | `preview_character_card` | len() against a limit | `if not isinstance(source, bytes) or not source or len(source) > MAX_CARD_BYTES:` | |
| `application/character_card_import.py:223` | `preview_character_card` | len() against a limit | `if not data["name"].strip() or len(data["name"].strip()) > 160:` | |
| `application/character_card_import.py:461` | `load_import_original` | len() against a limit | `or len(source) > MAX_CARD_BYTES` | |
| `application/character_card_import.py:524` | `_read_pending_preview` | len() against a limit | `if len(source) > MAX_CARD_BYTES or hashlib.sha256(source).hexdigest() != digest:` | |
| `application/character_card_import.py:590` | `stage_card_preview` | len() against a limit | `if len(tuple(root.iterdir())) >= MAX_PENDING_PREVIEWS:` | |
| `application/character_profiles.py:70` | `_parse` | len() against a limit | `if not data or len(data) > MAX_PROFILE_BYTES:` | |
| `application/character_profiles.py:170` | `validate_character_profile` | len() against a limit | `if len(canonical_json_bytes(value)) > MAX_PROFILE_BYTES:` | |
| `application/character_profiles.py:254` | `validate_source_profile` | len() against a limit | `or len(name) > 160` | |
| `application/character_profiles.py:344` | `save_character_profile` | len() against a limit | `if len(tuple(root.iterdir())) >= 256:` | |
| `application/character_profiles.py:383` | `character_profile_catalog` | len() against a limit | `if len(paths) > 256:` | |
| `application/character_rules.py:42` | `validate_character_rules` | len() against a limit | `if (not isinstance(value["description"], str) or not 1 <= len(value["description"]) <= 2000` | |
| `application/import_repair.py:119` | `_read_selected_source` | len() against a limit | `if len(value) > _SOURCE_BYTE_LIMIT:` | |
| `application/import_repair.py:148` | `_read_safe_zip` | len() against a limit | `if not infos or len(infos) > _MEMBER_COUNT_LIMIT:` | |
| `application/import_repair.py:380` | `_read_draft_files` | len() against a limit | `if len(value) > _MEMBER_BYTE_LIMIT:` | |
| `application/import_repair.py:383` | `_read_draft_files` | len() against a limit | `if total > _EXPANDED_BYTE_LIMIT or len(files) >= _MEMBER_COUNT_LIMIT:` | |
| `application/import_repair.py:391` | `_package_drafts` | len() against a limit | `if manifest_bytes is None or len(manifest_bytes) > _MANIFEST_LIMIT:` | |
| `application/initial_character_state.py:106` | `_calculation_contract` | len() against a limit | `or len(calculations) > _MAX_CALCULATIONS` | |
| `application/library_sources.py:60` | `LibrarySourceReader.candidates` | len() against a limit | `if len(results) > _MAX_CANDIDATES:` | |
| `application/library_sources.py:77` | `LibrarySourceReader.files` | len() against a limit | `if len(paths) > _MAX_TOTAL_PATHS:` | |
| `application/library_sources.py:160` | `LibrarySourceReader._package_index` | len() against a limit | `if len(candidate.package_lock.entries) > _MAX_PACKAGES:` | |
| `application/plan_bundle.py:84` | `_read_bundle` | len() against a limit | `if len(data) > PLAN_BUNDLE_BYTE_LIMIT:` | |
| `application/plan_network_admission.py:32` | `_reveal_records` | len() against a limit | `or len(identity.encode("utf-8")) > 128):` | |
| `application/plan_snapshot.py:61` | `validate_plan_snapshot` | len() against a limit | `if not isinstance(package_snapshot, bytes) or not 0 < len(package_snapshot) <= ( PLAN_SNAPSHOT_BYTE_LIMIT ):` | |
| `application/plan_state_admission.py:66` | `_matches` | len() against a limit | `if len(matches) >= 8192:` | |
| `application/reusable_participants.py:228` | `_validated_payload` | len() against a limit | `if len(encoded) > _MAX_PARTICIPANT_BYTES:` | |
| `application/reusable_participants.py:241` | `_validated_authored_profile` | len() against a limit | `or len(value.encode("utf-8")) > _MAX_PARTICIPANT_BYTES` | |
| `application/reusable_participants.py:264` | `parse_setting_character` | len() against a limit | `or len(source.encode("utf-8")) > _MAX_PARTICIPANT_BYTES` | |
| `application/reusable_participants.py:267` | `parse_setting_character` | len() against a limit | `or len(character_id) > 160` | |
| `application/reusable_participants.py:337` | `parse_setting_character` | len() against a limit | `or len(summary) > 500` | |
| `application/reusable_participants.py:377` | `parse_setting_character` | len() against a limit | `if not isinstance(name, str) or not name or name != name.strip() or len(name) > 160:` | |
| `application/reusable_participants.py:397` | `parse_setting_character` | len() against a limit | `if len(encoded) > _MAX_PARTICIPANT_BYTES:` | |
| `application/reusable_participants.py:438` | `validate_reusable_participant_draft` | len() against a limit | `or len(summary) > 500` | |
| `application/reusable_participants.py:455` | `validate_reusable_participant_draft` | len() against a limit | `if not isinstance(name, str) or name != name.strip() or not name or len(name) > 160:` | |
| `application/reusable_participants.py:736` | `load_reusable_participant` | len() against a limit | `or len(str(record["summary"])) > 500` | |
| `application/reusable_participants.py:777` | `compatible_reusable_participants` | len() against a limit | `if len(directories) > _MAX_LIBRARY_PARTICIPANTS:` | |
| `application/reusable_plans.py:310` | `save_reusable_rift_plan` | len() against a limit | `or len(title) > 160` | |
| `application/reusable_plans.py:441` | `load_reusable_campaign_plan` | len() against a limit | `if len(package_snapshot) > PLAN_SNAPSHOT_BYTE_LIMIT:` | |
| `application/reusable_plans.py:515` | `reusable_campaign_plan_catalog` | len() against a limit | `if len(directories) > _MAX_LIBRARY_PLANS:` | |
| `application/schema_admission.py:86` | `validate_json_schema` | len() against a limit | `or not 1 <= len(branches) <= 256` | |
| `application/setting_character_requirements.py:76` | `parse_setting_character_requirements` | len() against a limit | `if not encoded or len(encoded) > _BYTE_LIMIT:` | |
| `application/setting_character_requirements.py:145` | `validate_setting_character_data` | len() against a limit | `if len(canonical_json_bytes(captured)) > _BYTE_LIMIT:` | |
| `builder/_campaign_plan_materializer.py:54` | `_bounded_text` | len() against a limit | `if len(value) > CAMPAIGN_PLAN_BYTE_LIMIT:` | |
| `builder/_package_materializer.py:101` | `_read_descriptor` | len() against a limit | `if len(raw) > _DESCRIPTOR_BYTE_LIMIT:` | |
| `builder/_package_materializer.py:159` | `_read_payloads` | len() against a limit | `if len(retained_entries) >= _PAYLOAD_ENTRY_LIMIT:` | |
| `builder/_package_materializer.py:174` | `_read_payloads` | len() against a limit | `if len(Path(relative).parts) > _PAYLOAD_PATH_DEPTH_LIMIT:` | |
| `builder/_package_materializer.py:180` | `_read_payloads` | len() against a limit | `if len(payloads) >= _PAYLOAD_FILE_LIMIT:` | |
| `builder/_package_materializer.py:185` | `_read_payloads` | len() against a limit | `if len(value) > _PAYLOAD_BYTE_LIMIT or total_bytes > _PAYLOAD_BYTE_LIMIT:` | |
| `builder/_package_materializer.py:227` | `materialize_ruleset_draft` | len() against a limit | `if len(source_bytes) > _DESCRIPTOR_BYTE_LIMIT:` | |
| `builder/_package_record_edits.py:34` | `_read_record` | len() against a limit | `if len(raw) > _RECORD_BYTE_LIMIT:` | |
| `builder/_package_snapshots.py:101` | `_source_selection` | len() against a limit | `if len(raw) > 65536:` | |
| `builder/_plan_snapshot_materializer.py:168` | `_removed_paths` | len() against a limit | `or len(value) > _REMOVAL_COUNT_LIMIT` | |
| `campaign/rift.py:81` | `validate_rift_party_handoff` | len() against a limit | `or len(companions) > RIFT_RULESET_COMPANION_MAXIMUM` | |
| `campaign/rift.py:406` | `materialize_rift_selection` | len() against a limit | `if len(companion_ids) > RIFT_RULESET_COMPANION_MAXIMUM or len(set(companion_ids)) != len(` | |
| `campaign/rift.py:410` | `materialize_rift_selection` | len() against a limit | `if not isinstance(customization_notes, str) or len(customization_notes) > 4_000:` | |
| `domain/authority.py:652` | `mechanic_outcome_is_safe` | len() against a limit | `or not 2 <= len(choice.options) <= 16` | |
| `domain/authority.py:653` | `mechanic_outcome_is_safe` | len() against a limit | `or len(choice.prompt.encode("utf-8")) > 4096` | |
| `domain/authority.py:667` | `mechanic_outcome_is_safe` | len() against a limit | `or len(option.label.encode("utf-8")) > 1024` | |
| `domain/campaign_plan.py:219` | `_validate_party_placements` | len() against a limit | `or not 1 <= len(value) <= 64` | |
| `domain/campaign_plan.py:249` | `_validate_party_placements` | len() against a limit | `or len(appends) > 16` | |
| `domain/campaign_plan.py:335` | `parse_starting_party_policy` | len() against a limit | `or len(required) > 64` | |
| `domain/campaign_plan.py:502` | `validate_campaign_plan` | len() against a limit | `or len(mechanics) > 256` | |
| `domain/campaign_plan.py:525` | `validate_campaign_plan` | len() against a limit | `or len(entities) > 128` | |
| `domain/campaign_plan.py:555` | `validate_campaign_plan` | len() against a limit | `or len(facts) > 256` | |
| `domain/campaign_plan.py:580` | `validate_campaign_plan` | len() against a limit | `if len(references) > CAMPAIGN_PLAN_REFERENCE_LIMIT:` | |
| `domain/campaign_plan.py:590` | `validate_campaign_plan` | len() against a limit | `or not 1 <= len(structure) <= 64` | |
| `domain/campaign_plan.py:636` | `validate_campaign_plan` | len() against a limit | `or len(secrets) > 256` | |
| `domain/campaign_plan.py:741` | `validate_campaign_plan` | len() against a limit | `if len(markdown.encode("utf-8")) > CAMPAIGN_PLAN_BYTE_LIMIT:` | |
| `domain/campaign_plan.py:749` | `validate_campaign_plan` | len() against a limit | `if len(encoded) > CAMPAIGN_PLAN_BYTE_LIMIT:` | |
| `domain/campaign_sequel.py:45` | `prepare_story` | len() against a limit | `if any(len(item) > 160 for item in mapping.values()):` | |
| `domain/campaign_sequel.py:67` | `review_payload` | len() against a limit | `if not isinstance(note, str) or not note.strip() or len(note) > 4000:` | |
| `domain/content_validation.py:138` | `validate_content_shape` | len() against a limit | `if len(corrections) >= 16:` | |
| `domain/content_validation.py:140` | `validate_content_shape` | len() against a limit | `if len(corrections) >= 16:` | |
| `domain/errors.py:150` | `MechanicsRejected.__init__` | len() against a limit | `or len(reason) > 500 or any(ord(character) < 32 for character in reason)` | |
| `domain/errors.py:163` | `RulesetPreconditionRejected.__init__` | len() against a limit | `or len(safe_reason) > 500` | |
| `domain/interview_contribution.py:76` | `parse_interview_contribution_bytes` | len() against a limit | `if not value or len(value) > INTERVIEW_CONTRIBUTION_BYTE_LIMIT:` | |
| `domain/inventory.py:125` | `validate_holdings` | len() against a limit | `if not isinstance(name, str) or not name.strip() or name != name.strip() or len(name) > 300:` | |
| `domain/mechanics_choices.py:63` | `answers_from_payload` | len() against a limit | `if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) >= 64:` | |
| `domain/no_ruleset_plan.py:79` | `validate_no_ruleset_world` | len() against a limit | `if not isinstance(name, str) or not name.strip() or name != name.strip() or len(name) > 300:` | |
| `domain/package_library.py:114` | `_validate_mechanical_inventory_declaration` | len() against a limit | `if not 1 <= len(names) <= 32:` | |
| `domain/package_library.py:803` | `_safe_small_label` | len() against a limit | `if len(value.encode("utf-8")) > 256 or "/" in value or "\\" in value or ":" in value:` | |
| `domain/package_library.py:818` | `_sorted_unique_identities` | len() against a limit | `if len(item.encode("utf-8")) > 512:` | |
| `domain/package_library.py:877` | `_content_sources` | len() against a limit | `or len(item.encode("utf-8")) > 256` | |
| `domain/package_library.py:964` | `parse_campaign_setting_knowledge_bytes` | len() against a limit | `if len(value) > CAMPAIGN_SETTING_KNOWLEDGE_BYTE_LIMIT:` | |
| `domain/package_library.py:1019` | `parse_campaign_setting_knowledge_bytes` | len() against a limit | `or len(text.encode("utf-8")) > 4_000` | |
| `domain/package_library.py:1063` | `parse_gameplay_support_bytes` | len() against a limit | `if len(value) > GAMEPLAY_SUPPORT_BYTE_LIMIT:` | |
| `domain/package_library.py:1155` | `parse_content_declarations_bytes` | len() against a limit | `if len(value) > CONTENT_DECLARATION_BYTE_LIMIT:` | |
| `domain/package_library.py:1561` | `_surface_source` | len() against a limit | `if not kinds or len(kinds) > 16 or any(not isinstance(item, str) for item in kinds):` | |
| `domain/package_library.py:1570` | `_surface_source` | len() against a limit | `if not path or len(path) > 16 or any(not isinstance(item, str) for item in path):` | |
| `domain/package_library.py:1577` | `_surface_source` | len() against a limit | `if not entries or len(entries) > 16:` | |
| `domain/package_library.py:1587` | `_surface_source` | len() against a limit | `if not fields or len(fields) > 16:` | |
| `domain/package_library.py:1698` | `_ui_surface` | len() against a limit | `or len(bindings) > 16` | |
| `domain/package_library.py:1713` | `_ui_surface` | len() against a limit | `if len(actions) > 16:` | |
| `domain/panel_layout.py:18` | `parse_panel_layout` | len() against a limit | `if len(value) > PANEL_LAYOUT_BYTE_LIMIT:` | |
| `domain/panel_layout.py:53` | `parse_panel_layout.tree` | len() against a limit | `if not isinstance(value, Mapping) or depth > 16 or len(node_ids) >= 256:` | |
| `domain/panel_layout.py:84` | `parse_panel_layout` | len() against a limit | `if len(title.encode("utf-8")) > 256:` | |
| `domain/session.py:252` | `SessionRecord.__post_init__` | len() against a limit | `not isinstance(value, str) or not 1 <= len(value) <= 32` | |
| `domain/session.py:263` | `SessionRecord.__post_init__` | len() against a limit | `if not self.scope or len(self.scope) > 8 or any(not item for item in self.scope):` | |
| `domain/session.py:479` | `BuilderChatRecord.__post_init__` | len() against a limit | `if len(self.display_name) > 120:` | |
| `domain/starting_state.py:36` | `_path` | len() against a limit | `or not 1 <= len(value) <= 16):` | |
| `domain/starting_state.py:39` | `_path` | len() against a limit | `if not isinstance(part, str) or not part or len(part) > 128:` | |
| `domain/starting_state.py:51` | `parse_state_schemas` | len() against a limit | `or not 1 <= len(value) <= 32):` | |
| `domain/starting_state.py:66` | `parse_state_schemas` | len() against a limit | `or len(references) > 128):` | |

## Lane 2: Game engine

| Where | Function | Kind | Code | Done |
| --- | --- | --- | --- | --- |
| `application/_runtime_cache.py:66` | `cached_runtime` | len() against a limit | `if len(document) <= _MAX_CACHE_BYTES:` | |
| `application/action_sequences.py:433` | `compose` | len() against a limit | `or not 0 <= len(steps) <= 32` | |
| `application/actor_decisions.py:127` | `project_actor_decision` | len() against a limit | `or len(persona.encode("utf-8")) > 8192` | |
| `application/actor_decisions.py:130` | `project_actor_decision` | len() against a limit | `or not 1 <= len(rows) <= 256` | |
| `application/actor_decisions.py:140` | `project_actor_decision` | len() against a limit | `not isinstance(row[key], str) or not row[key] or len(row[key].encode()) > 256` | |
| `application/decision_scope.py:109` | `_project_side_scope` | len() against a limit | `or len(eligible_value) > 128` | |
| `application/decision_scope.py:221` | `project_campaign_preparation_scope` | len() against a limit | `or len(queue) > 64 or not all(isinstance(event, Mapping) for event in queue)):` | |
| `application/package_runtime.py:152` | `_validate_shape.visit` | len() against a limit | `if len(item) > _MAX_OBJECT_MEMBERS:` | |
| `application/package_runtime.py:420` | `AuthorityMemoryStateBridge._record_spatial_evidence` | len() against a limit | `if len(self._spatial_evidence) >= _MAX_SPATIAL_EVIDENCE:` | |
| `application/package_runtime.py:423` | `AuthorityMemoryStateBridge._record_spatial_evidence` | len() against a limit | `if len(result_bytes) > _MAX_SPATIAL_RESULT_BYTES:` | |
| `application/package_runtime.py:563` | `AuthorityMemoryStateBridge.record_shot_effect` | len() against a limit | `if not isinstance(request, Mapping) or len(self._shot_effects) >= 256:` | |
| `application/package_runtime.py:917` | `_parse_runtime` | len() against a limit | `while len(_PARSED_RUNTIMES) > 16:` | |
| `application/package_runtime.py:925` | `_load_runtime` | len() against a limit | `if len(runtime_bytes) > PACKAGE_RUNTIME_BYTE_LIMIT:` | |
| `application/package_runtime.py:1126` | `package_tool_decision_title` | len() against a limit | `return title.strip() if isinstance(title, str) and 0 < len(title) <= 256 else ""` | |
| `application/player_actions.py:25` | `_valid_control_queries` | len() against a limit | `if (not isinstance(queries, (list, tuple)) or len(queries) > 8` | |
| `application/player_actions.py:26` | `_valid_control_queries` | len() against a limit | `or not isinstance(calculations, (list, tuple)) or len(calculations) > 24):` | |
| `application/player_actions.py:51` | `_valid_control_queries` | len() against a limit | `or not isinstance(expression, str) or not 0 < len(expression) <= 500` | |
| `application/player_actions.py:52` | `_valid_control_queries` | len() against a limit | `or not isinstance(condition, str) or not 0 < len(condition) <= 500):` | |
| `application/player_actions.py:160` | `parse_player_controls` | len() against a limit | `if (not isinstance(checks, (list, tuple)) or len(checks) > 32 or any(` | |
| `application/player_actions.py:163` | `parse_player_controls` | len() against a limit | `or len(check["condition"]) > 4096` | |
| `application/player_actions.py:165` | `parse_player_controls` | len() against a limit | `or len(check["reason"]) > 256` | |
| `application/player_actions.py:175` | `parse_player_controls` | len() against a limit | `or len(actor_parameter) > 128` | |
| `application/result_card_admission.py:14` | `validate_activity_label` | len() against a limit | `if isinstance(value, str) and value.strip() and len(value) <= 80:` | |
| `application/result_card_admission.py:17` | `validate_activity_label` | len() against a limit | `and isinstance(value["path"], str) and 1 <= len(value["path"]) <= 256):` | |
| `application/result_card_admission.py:64` | `_value_issues` | len() against a limit | `if len(parts) > 16 or any(not part for part in parts):` | |
| `application/result_cards.py:34` | `_scalar` | len() against a limit | `if isinstance(value, str) and len(value) > 4000:` | |
| `application/result_cards.py:45` | `_public_value` | len() against a limit | `if len(path) > 16 or path[0] not in {"parameters", "context"}:` | |
| `application/result_cards.py:64` | `_value` | len() against a limit | `if not isinstance(labels, Mapping) or len(labels) > 64:` | |
| `application/result_cards.py:73` | `_value` | len() against a limit | `if len(value) > 64:` | |
| `application/result_cards.py:153` | `_equation` | len() against a limit | `if not isinstance(rolls, list) or not 1 <= len(rolls) <= 64 or any(` | |
| `application/result_cards.py:216` | `project_result_card` | len() against a limit | `if not isinstance(raw_actions, list) or len(raw_actions) > 8:` | |
| `application/result_cards.py:226` | `project_result_card` | len() against a limit | `if not isinstance(raw_parameters, Mapping) or len(raw_parameters) > 64:` | |
| `application/shot_effects.py:325` | `project_shot_effect` | len() against a limit | `or len(contacts_value) > 64` | |
| `application/spatial_preview.py:24` | `project_preflight_path` | len() against a limit | `or not 2 <= len(points) <= 1024` | |
| `application/tactical_grid.py:171` | `_public_display` | len() against a limit | `or len(label.encode("utf-8")) > _MAX_LABEL_BYTES` | |
| `application/tactical_grid.py:175` | `_public_display` | len() against a limit | `or len(kind.encode("utf-8")) > 64` | |
| `application/tactical_grid.py:186` | `_public_display` | len() against a limit | `or len(item.encode("utf-8")) > _MAX_LABEL_BYTES` | |
| `application/tactical_grid.py:231` | `_project_objectives` | len() against a limit | `if items is None or len(items) > 256:` | |
| `application/tactical_grid.py:250` | `_project_objectives` | len() against a limit | `or len(label.encode("utf-8")) > _MAX_LABEL_BYTES` | |
| `application/tactical_grid.py:296` | `_project_preview` | len() against a limit | `if zones is not None and len(zones) <= 256 and all(isinstance(item, str) for item in zones):` | |
| `application/tactical_grid.py:447` | `project_coordinate_map` | len() against a limit | `and reference and len(reference.encode('utf-8')) <= _MAX_LABEL_BYTES):` | |
| `application/tactical_grid.py:473` | `_project_occupied_space` | len() against a limit | `if (not isinstance(widths, (list, tuple)) or not 1 <= len(widths) <= 32` | |
| `application/tactical_grid.py:476` | `_project_occupied_space` | len() against a limit | `or not isinstance(convention, str) or not 1 <= len(convention) <= 128):` | |
| `application/tactical_grid.py:540` | `_selection_value_valid` | len() against a limit | `and 2 <= len(items) <= _MAX_PATH_POINTS` | |
| `runtime/_collections.py:15` | `collection` | len() against a limit | `if not isinstance(value, (list, tuple)) or len(value) > LIMIT:` | |
| `runtime/_collections.py:34` | `joined` | len() against a limit | `if len(values) > LIMIT:` | |
| `runtime/_collections.py:61` | `text` | len() against a limit | `if not isinstance(value, (str, int, float, bool)) or len(str(value)) > 256:` | |
| `runtime/_collections.py:90` | `partitions` | len() against a limit | `if len(values) > 32 or len(rows) > 128 or len(required) > 8:` | |
| `runtime/_collections.py:110` | `partitions.visit` | len() against a limit | `if work > 16384 or len(found) > 256:` | |
| `runtime/_collections.py:140` | `completions` | len() against a limit | `if len(candidates) > 128:` | |
| `runtime/_effect_executor.py:56` | `_handle_publish` | len() against a limit | `if len(body["fields"]) > 32:` | |
| `runtime/_effect_executor.py:399` | `_handle_request_choice` | len() against a limit | `if not 2 <= len(options) <= 16:` | |
| `runtime/_effect_executor.py:401` | `_handle_request_choice` | len() against a limit | `if len(rendered_prompt.encode("utf-8")) > 4096 or any(` | |
| `runtime/_effect_executor.py:402` | `_handle_request_choice` | len() against a limit | `len(option.label.encode("utf-8")) > 1024 for option in options` | |
| `runtime/_effect_executor.py:413` | `_handle_request_choice` | len() against a limit | `if len(control.prior_answers) >= 64:` | |
| `runtime/_effect_executor.py:423` | `_handle_request_choice` | len() against a limit | `if len(control.prior_answers) >= 63:` | |
| `runtime/_expression_parser.py:164` | `tokenize` | len() against a limit | `if len(expr) > MAX_EXPRESSION_LENGTH:` | |
| `runtime/_expression_parser.py:206` | `tokenize` | len() against a limit | `if len(value) > MAX_STRING_LENGTH:` | |
| `runtime/_initiative.py:86` | `roll_initiative` | len() against a limit | `if not isinstance(label, str) or not label.strip() or len(label) > 200:` | |
| `runtime/_scene_awareness.py:368` | `SceneAwarenessBuilder._read_faction_cs` | len() against a limit | `if len(text) > 300:` | |
| `runtime/_schema_validator.py:789` | `_validate_campaign_preparation` | len() against a limit | `or any(not isinstance(value[name], str) or not 1 <= len(value[name]) <= 2000` | |
| `runtime/_schema_validator.py:850` | `_validate_model_decision_boundary` | len() against a limit | `or len(isolated["instructions"].encode("utf-8")) > 8192):` | |
| `runtime/_schema_validator.py:856` | `_validate_model_decision_boundary` | len() against a limit | `if (not isinstance(path, list) or len(path) > 8` | |
| `runtime/_schema_validator.py:1355` | `_validate_spatial_query_effect` | len() against a limit | `isinstance(circles, list) and len(circles) <= 64` | |
| `runtime/_schema_validator.py:1364` | `_validate_spatial_query_effect` | len() against a limit | `isinstance(edge_costs, list) and len(edge_costs) <= 16384` | |
| `runtime/_schema_validator.py:1394` | `_validate_spatial_query_effect` | len() against a limit | `and len(excluded) <= 64` | |
| `runtime/_schema_validator.py:1439` | `_validate_spatial_query_effect` | len() against a limit | `and len(excluded) <= 64` | |
| `runtime/_schema_validator.py:1492` | `_validate_spatial_query_effect` | len() against a limit | `and 2 <= len(value) <= 256` | |
| `runtime/_schema_validator.py:1610` | `_validate_request_choice_effect` | len() against a limit | `if not isinstance(prompt, str) or not prompt or len(prompt.encode("utf-8")) > 4096:` | |
| `runtime/_schema_validator.py:1617` | `_validate_request_choice_effect` | len() against a limit | `if not isinstance(options, list) or not 2 <= len(options) <= 16:` | |
| `runtime/_schema_validator.py:1635` | `_validate_request_choice_effect` | len() against a limit | `if not isinstance(label, str) or not label or len(label.encode("utf-8")) > 1024:` | |
| `runtime/_tool_executor.py:211` | `prepare_tool_context` | len() against a limit | `if error_msg not in failures and len(failures) < 64:` | |
| `runtime/_tool_executor.py:218` | `prepare_tool_context` | len() against a limit | `if evaluation_error is not None and len(failures) < 64:` | |
| `spatial/_engine.py:1496` | `_resolve_circle_route` | len() against a limit | `if len(blocking) > 16:` | |
| `spatial/_engine.py:1825` | `SpatialEngine.replace_scene` | len() against a limit | `while len(self._compiled) > _CACHE_LIMIT:` | |
| `spatial/_engine.py:1863` | `SpatialEngine.query` | len() against a limit | `while len(self._cache) > _CACHE_LIMIT:` | |
| `spatial/_interface.py:457` | `_metadata` | len() against a limit | `if len(canonical_json_bytes(payload)) > _MAX_METADATA_BYTES:` | |
| `spatial/_interface.py:1031` | `_metadata_selector` | len() against a limit | `or len(selector["key"]) > 128` | |
| `spatial/_interface.py:1033` | `_metadata_selector` | len() against a limit | `or len(selector["value"]) > 128` | |
| `spatial/_interface.py:1287` | `admit_spatial_query` | len() against a limit | `or len(selector["key"]) > 128` | |
| `spatial/_interface.py:1289` | `admit_spatial_query` | len() against a limit | `or len(selector["value"]) > 128` | |

## Lane 3: Storage and authority

| Where | Function | Kind | Code | Done |
| --- | --- | --- | --- | --- |
| `application/authority.py:354` | `_markdown_section_references` | len() against a limit | `if len(references) >= 100 or len(slug) > 200 or counts[slug] != 1:` | |
| `application/authority.py:890` | `AuthorityOwner._package_tool` | len() against a limit | `if len(self._runtime_payloads) >= 16:` | |
| `application/authority.py:1317` | `AuthorityOwner.record_campaign_scene` | len() against a limit | `if not summary or summary != summary.strip() or len(summary) > 2_000:` | |
| `application/authority.py:1453` | `AuthorityOwner.record_campaign_memory` | len() against a limit | `if not text or text != text.strip() or len(text) > 2_000:` | |
| `application/authority.py:1551` | `AuthorityOwner.record_campaign_entity` | len() against a limit | `if not name or name != name.strip() or len(name) > 300:` | |
| `application/authority.py:1625` | `AuthorityOwner.record_campaign_plan_progress` | len() against a limit | `if not summary or summary != summary.strip() or len(summary) > 2_000:` | |
| `application/authority.py:1695` | `AuthorityOwner.update_campaign_file` | len() against a limit | `if not all_updates or len(all_updates) > 64:` | |
| `application/authority.py:1897` | `AuthorityOwner.update_campaign_file` | len() against a limit | `or len(text) > 2_000` | |
| `application/authority.py:1962` | `AuthorityOwner.update_campaign_file` | len() against a limit | `or len(name) > 300` | |
| `application/authority.py:2020` | `AuthorityOwner.update_campaign_file` | len() against a limit | `or len(summary) > 2_000` | |
| `application/authority.py:2093` | `AuthorityOwner.update_campaign_file` | len() against a limit | `or len(progress_summary) > 2_000` | |
| `application/authority.py:3603` | `AuthorityOwner.advance_guided_campaign_draft` | len() against a limit | `or len(visible_answer) > _MAX_GUIDED_VISIBLE_ANSWER_CHARS` | |
| `application/authority.py:3819` | `AuthorityOwner.start_interview_builder_chat` | len() against a limit | `or len(visible_answer) > _MAX_GUIDED_VISIBLE_ANSWER_CHARS` | |
| `application/authority.py:3902` | `AuthorityOwner.start_guided_creation_subflow` | len() against a limit | `or len(visible_answer) > _MAX_GUIDED_VISIBLE_ANSWER_CHARS` | |
| `application/authority.py:5650` | `AuthorityOwner.search_campaign_knowledge` | len() against a limit | `if len(query) > 500:` | |
| `application/authority.py:5685` | `AuthorityOwner.search_campaign_knowledge.add_match` | len() against a limit | `if isinstance(heading, str) and len(heading) > 200:` | |
| `application/authority.py:5695` | `AuthorityOwner.search_campaign_knowledge.add_match` | len() against a limit | `**({"label_truncated": True} if len(label) > 200 else {}),` | |
| `application/authority.py:5700` | `AuthorityOwner.search_campaign_knowledge.add_match` | len() against a limit | `if len(label) > 200` | |
| `application/authority.py:5890` | `AuthorityOwner.search_campaign_knowledge` | len() against a limit | `if len(unavailable_references) < 8:` | |
| `application/authority.py:5905` | `AuthorityOwner.search_campaign_knowledge` | len() against a limit | `if len(unavailable_references) < 8:` | |
| `application/authority.py:5923` | `AuthorityOwner.search_campaign_knowledge` | len() against a limit | `if len(value) <= REFERENCE_CONTENT_BYTE_LIMIT and path.endswith(".md")` | |
| `application/authority.py:5926` | `AuthorityOwner.search_campaign_knowledge` | len() against a limit | `if len(value) > REFERENCE_CONTENT_BYTE_LIMIT or any(` | |
| `application/authority.py:5927` | `AuthorityOwner.search_campaign_knowledge` | len() against a limit | `len(heading) > 200 or len(slug) > 200 for heading, slug, _ in sections` | |
| `application/authority.py:6555` | `AuthorityOwner.read_campaign_reference` | len() against a limit | `if len(canonical_json_bytes(payload)) > REFERENCE_REPLY_BYTE_LIMIT:` | |
| `application/authority.py:7079` | `AuthorityOwner._resolve_card_control` | len() against a limit | `or not isinstance(actions, Sequence) or len(actions) > 8 or index >= len(actions)):` | |
| `application/authority.py:7105` | `AuthorityOwner._card_control_readiness` | len() against a limit | `or not isinstance(actions, Sequence) or len(actions) > 8):` | |
| `application/authority.py:7159` | `AuthorityOwner.player_action_availability` | len() against a limit | `if (not isinstance(requests, (list, tuple)) or len(requests) > 256` | |
| `application/campaign_inventory.py:186` | `prepare_inventory_payload` | len() against a limit | `or len(collection) > 64` | |
| `application/campaign_rules_update.py:551` | `_preview.report` | len() against a limit | `if len(conflicts) < 32:` | |
| `application/campaign_sequels.py:63` | `preview` | len() against a limit | `if len(rows) >= MAX_ADVENTURES:` | |
| `infrastructure/builder_source_ingestion.py:174` | `ingest_builder_sources` | len() against a limit | `if not 1 <= len(source_paths) <= MAX_SOURCE_FILES:` | |
| `infrastructure/campaign_archive.py:2975` | `_discover_recovery_manifest` | len() against a limit | `len(info.filename.encode("utf-8")) > RECOVERY_MAX_MEMBER_NAME_BYTES for info in infos` | |
| `infrastructure/private_records_archive.py:43` | `validate_rows` | len() against a limit | `if len(result) >= MAX_ROWS or not isinstance(row, Mapping) or set(row) != set(FIELDS):` | |
| `infrastructure/private_records_archive.py:83` | `encode` | len() against a limit | `if len(line) > MAX_ROW_BYTES or len(output) + len(line) > MAX_BYTES:` | |
| `infrastructure/private_records_archive.py:91` | `decode` | len() against a limit | `if len(data) > MAX_BYTES or (data and not data.endswith(b"\n")):` | |
| `infrastructure/private_records_archive.py:95` | `decode` | len() against a limit | `if len(line) > MAX_ROW_BYTES or len(rows) >= MAX_ROWS:` | |
| `infrastructure/sqlite_authority.py:1737` | `SQLiteAuthorityStore._write_export_blob` | len() against a limit | `or len(data) > campaign_archive.STREAM_CHUNK_BYTES` | |
| `infrastructure/sqlite_authority.py:7403` | `SQLiteAuthorityStore.commit_campaign_continuity_batch` | len() against a limit | `if len(admitted) > 64:` | |
| `infrastructure/sqlite_authority.py:12867` | `SQLiteAuthorityStore.consume_package_blob` | len() against a limit | `or len(data) > PACKAGE_CHUNK_LIMIT` | |
| `infrastructure/sqlite_authority.py:13435` | `SQLiteAuthorityStore.advance_mechanics_choice` | len() against a limit | `if (len(pending.prior_answers) >= 63 or answer.request != pending.choice` | |
| `infrastructure/sqlite_sequel.py:32` | `adventure_rows` | len() against a limit | `if row["plan_id"] in seen or len(result) >= MAX_ADVENTURES:` | |
| `infrastructure/sqlite_session.py:586` | `interview_name` | len() against a limit | `return name.strip() if isinstance(name, str) and 0 < len(name.strip()) <= 120 else None` | |

## Lane 4: Providers and agents

| Where | Function | Kind | Code | Done |
| --- | --- | --- | --- | --- |
| `agent/_builder_session.py:434` | `_builder_reference_tool.inspect_page` | len() against a limit | `if not isinstance(query, str) or len(query) > 120:` | |
| `agent/_builder_session.py:448` | `_builder_reference_tool.inspect_page` | len() against a limit | `not isinstance(query, str) or not query or len(query) > 120` | |
| `agent/_builder_session.py:844` | `_campaign_content_tools.discover` | len() against a limit | `if not isinstance(query, str) or len(query) > 160:` | |
| `agent/_builder_session.py:960` | `_creation_subflow_completion_tool.complete` | len() against a limit | `or len(summary) > 2000` | |
| `agent/_builder_session.py:962` | `_creation_subflow_completion_tool.complete` | len() against a limit | `or len(conflicts) > 16` | |
| `agent/_builder_session.py:964` | `_creation_subflow_completion_tool.complete` | len() against a limit | `not isinstance(item, str) or not item.strip() or len(item) > 500` | |
| `agent/_builder_session.py:1563` | `_campaign_source_tool.inspect` | len() against a limit | `if path is not None or not isinstance(query, str) or len(query) > 120:` | |
| `agent/_builder_title.py:17` | `opening_title` | len() against a limit | `if len(normalized) <= TITLE_LIMIT:` | |
| `agent/_builder_title.py:47` | `FirstReplyTitle.feed` | len() against a limit | `if 0 < len(title) <= TITLE_LIMIT and not any(c in title for c in "<>"):` | |
| `agent/_builder_title.py:53` | `FirstReplyTitle.feed` | len() against a limit | `if not final and len(candidate) <= 256:` | |
| `agent/_builder_title.py:60` | `FirstReplyTitle.feed` | len() against a limit | `if not final and len(self._prefix) <= 256 and TITLE_PREFIX.startswith(candidate):` | |
| `agent/_campaign_memory.py:70` | `select_campaign_memory` | len() against a limit | `if len(selected) < _MAX_MEMORY_RECORDS and used_bytes + size <= _MAX_MEMORY_BYTES:` | |
| `agent/_character_profile_tools.py:142` | `character_profile_tools.execute` | len() against a limit | `if len(json.dumps(value, ensure_ascii=False).encode()) > 65536:` | |
| `agent/_library_source_tool.py:46` | `library_source_tool.inspect` | len() against a limit | `if not isinstance(query, str) or len(query) > 120:` | |
| `agent/_participant_file_tool.py:41` | `participant_file_tool.use_file` | len() against a limit | `if len(data) > _MAX_DRAFT_BYTES:` | |
| `agent/_provider_session.py:1278` | `_validated_speaker_name` | len() against a limit | `or len(name) > _SPEAKER_NAME_MAX_LENGTH` | |
| `agent/_provider_session.py:1748` | `ProviderSessionAdapter.__init__` | len() against a limit | `if not gm_system_prompt.strip() or len(encoded_prompt) > _MAX_GM_SYSTEM_PROMPT_BYTES:` | |
| `agent/_provider_session.py:1774` | `ProviderSessionAdapter.__init__` | len() against a limit | `if not campaign_plan_text.strip() or len(encoded_plan) > _MAX_CAMPAIGN_PLAN_BYTES:` | |
| `agent/_runner.py:677` | `AgentRunner._turn_impl` | len() against a limit | `input_preview = user_message[:50] + "..." if len(user_message) > 50 else user_message` | |
| `agent/_runner.py:2063` | `AgentRunner._build_chapter_history_text` | len() against a limit | `if len(content) > 800:` | |
| `agent/_stateless_mcp.py:943` | `_campaign_file_updates` | len() against a limit | `if len(raw_items) > 64:` | |
| `agent/_stateless_mcp.py:1005` | `_campaign_file_updates` | len() against a limit | `if len(updates) > 64:` | |
| `agent/_tool_loader.py:258` | `_summary` | len() against a limit | `if len(description) <= SUMMARY_LIMIT:` | |
| `agent/_tool_loader.py:275` | `definitions_record` | len() against a limit | `if len(text) <= INLINE_LIMIT:` | |
| `agent/_working_context.py:118` | `_valid_checkpoint` | len() against a limit | `and 0 < len(record["summary"]) <= _SUMMARY_LIMIT` | |
| `application/character_chat.py:133` | `_decode` | len() against a limit | `if not isinstance(raw_messages, list) or len(raw_messages) > MAX_MESSAGES:` | |
| `application/character_chat.py:202` | `_read` | len() against a limit | `if len(data) > MAX_FILE_BYTES:` | |
| `application/character_chat.py:215` | `_write` | len() against a limit | `if len(data) > MAX_FILE_BYTES:` | |
| `application/character_chat.py:375` | `_user_name` | len() against a limit | `not isinstance(value, str) or not value.strip() or len(value.strip()) > MAX_USER_NAME` | |
| `application/character_chat.py:395` | `create_chat` | len() against a limit | `if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 200:` | |
| `application/character_chat.py:412` | `create_chat` | len() against a limit | `if root.exists() and len(tuple(root.glob("character-chat-*.json"))) >= MAX_CHATS:` | |
| `application/character_chat.py:616` | `send_message` | len() against a limit | `if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 200:` | |
| `application/character_chat.py:618` | `send_message` | len() against a limit | `if not isinstance(text, str) or not text.strip() or len(text) > MAX_USER_TEXT:` | |
| `application/character_chat.py:667` | `send_message` | len() against a limit | `if len(reply) > MAX_REPLY_TEXT:` | |
| `config/_credential_protection.py:148` | `_read_key` | len() against a limit | `while len(key) <= _KEY_BYTES:` | |
| `config/_credential_protection.py:225` | `_key_file_seal` | len() against a limit | `if not _NONCE_BYTES + _TAG_BYTES <= len(value) <= _SECRET_BYTE_LIMIT:` | |
| `config/_credential_protection.py:237` | `_key_file_seal` | len() against a limit | `if len(result) > _SECRET_BYTE_LIMIT:` | |
| `config/provider_settings.py:467` | `_display_name` | len() against a limit | `if len(candidate) > 120 or any(ord(character) < 32 for character in candidate):` | |
| `config/provider_settings.py:571` | `_private_headers` | len() against a limit | `if not isinstance(value, Mapping) or len(value) > _HEADER_COUNT_LIMIT:` | |
| `config/provider_settings.py:578` | `_private_headers` | len() against a limit | `if not isinstance(raw_value, str) or not raw_value or len(raw_value) > 1024:` | |
| `config/provider_settings.py:639` | `require_configured_model` | len() against a limit | `or len(model_id) > 512` | |
| `config/provider_settings.py:829` | `_discovery_model` | len() against a limit | `if not model_id or len(model_id) > 256 or any(ord(character) < 32 for character in model_id):` | |
| `config/provider_settings.py:912` | `_response_document` | len() against a limit | `if response.status_code != 200 or len(response.content) > _DISCOVERY_RESPONSE_LIMIT:` | |
| `config/provider_settings.py:975` | `_lm_studio_capacity_models` | len() against a limit | `if not isinstance(rows, list) or len(rows) > _DISCOVERY_MODEL_LIMIT:` | |
| `config/provider_settings.py:983` | `_lm_studio_capacity_models` | len() against a limit | `if not isinstance(key, str) or not key or len(key) > 256:` | |
| `config/provider_settings.py:1235` | `_model_catalog` | len() against a limit | `if len(rows) > _DISCOVERY_MODEL_LIMIT:` | |
| `config/provider_settings.py:1301` | `discover_provider_models` | len() against a limit | `if len(search) > 256:` | |
| `config/provider_settings.py:2126` | `set_session_speed_settings` | len() against a limit | `or len(session_id) > 512` | |
| `config/provider_settings.py:2163` | `set_session_model_once_settings` | len() against a limit | `or len(session_id) > 512` | |
| `provider/_claude_install.py:175` | `verify_manifest` | len() against a limit | `primary = {(fields[11] if len(fields) > 11 else fields[2]).upper() for fields in valid}` | |
| `provider/_claude_runtime.py:379` | `ClaudeConnection._read` | len() against a limit | `if len(line.encode("utf-8")) > MAX_FRAME_BYTES or not line.endswith("\n"):` | |
| `provider/_lifecycle.py:38` | `LifecycleTiming.record` | len() against a limit | `if len(self._spans) < 24:` | |
| `provider/_openai_compat.py:644` | `_responses_service_tier` | len() against a limit | `if isinstance(value, str) and 0 < len(value) <= 32 and value.isascii():` | |
| `provider/_openai_compat.py:855` | `OpenAICompatProvider._response_continuation_options` | len() against a limit | `and len(context_id) <= 256):` | |
| `provider/_reasoning.py:41` | `reasoning_values` | len() against a limit | `or len(values) > 16` | |
| `provider/_retry_after.py:13` | `retry_after_seconds` | len() against a limit | `if not isinstance(value, str) or len(value) > 128:` | |
| `provider/_speed.py:53` | `speed_options` | len() against a limit | `if not isinstance(values, (list, tuple)) or len(values) > 8:` | |
| `provider/_speed.py:64` | `speed_options` | len() against a limit | `or not isinstance(name, str) or not name.strip() or len(name) > 40` | |
| `provider/_speed.py:65` | `speed_options` | len() against a limit | `or not isinstance(description, str) or len(description) > 200` | |
| `session/_context_compressor.py:117` | `ExtractiveCompressor.compress` | len() against a limit | `if isinstance(content, str) and len(content) > 200:` | |
| `session/_context_compressor.py:139` | `ExtractiveCompressor.compress` | len() against a limit | `if role == "user" and content and len(content) < 200:` | |
| `session/_context_compressor.py:213` | `ExtractiveCompressor._extract_narrative_fact` | len() against a limit | `if not content or len(content) < 10:` | |
| `session/_context_compressor.py:218` | `ExtractiveCompressor._extract_narrative_fact` | len() against a limit | `if len(first_sentence) > 15:` | |
| `session/_context_compressor.py:359` | `LLMCompressor._format_for_compression` | len() against a limit | `display = content[:300] + "..." if len(content) > 300 else content` | |
| `session/_context_compressor.py:486` | `ContextCompressor.compress` | len() against a limit | `if len(to_compress) < 20 or self._llm is None:` | |
| `session/_prompt_assembler.py:521` | `PromptAssembler.build_character` | len() against a limit | `if len(pc_block) > 200:` | |
| `session/_prompt_assembler.py:543` | `PromptAssembler.build_character` | len() against a limit | `if len(npc_block) > 600:` | |
| `session/_prompt_assembler.py:661` | `PromptAssembler._build_npc_block` | len() against a limit | `if len(candidate_line) > 120:` | |
| `session/_prompt_assembler.py:767` | `PromptAssembler.build_history_with_indices` | len() against a limit | `if len(content) > 500:` | |
| `session/_reveal_guard.py:110` | `RevealGuard.build_do_not_reveal_section` | len() against a limit | `if len(result) > 2000:` | |
| `tools/_builder_handlers.py:125` | `tool_read_file` | len() against a limit | `if len(content) > 100_000:` | |
| `tools/_builder_handlers.py:307` | `tool_search_files` | len() against a limit | `"content_complete": len(line) <= 200,` | |
| `tools/_builder_handlers.py:310` | `tool_search_files` | len() against a limit | `if len(results) >= 100:` | |
| `tools/_builder_handlers.py:312` | `tool_search_files` | len() against a limit | `if len(results) >= 100:` | |

## Lane 5: Owner, clients and release

| Where | Function | Kind | Code | Done |
| --- | --- | --- | --- | --- |
| `application/desktop_update/checkpoint.py:133` | `schema_identity` | len() against a limit | `if len(schema) > 5000:` | |
| `application/desktop_update/checkpoint.py:138` | `schema_identity` | len() against a limit | `if len(raw) > MANIFEST_LIMIT:` | |
| `application/desktop_update/discovery.py:59` | `discover` | len() against a limit | `if not isinstance(releases, list) or len(releases) > 30:` | |
| `application/desktop_update/discovery.py:96` | `discover` | len() against a limit | `if not isinstance(assets, list) or len(assets) > 128:` | |
| `application/desktop_update/drafts.py:29` | `validate_drafts` | len() against a limit | `or len(value["sessions"]) > MAX_SESSIONS` | |
| `application/desktop_update/drafts.py:57` | `validate_drafts` | len() against a limit | `or len(row["questions"]) > MAX_QUESTIONS` | |
| `application/desktop_update/drafts.py:75` | `validate_drafts` | len() against a limit | `if len(canonical_bytes(value)) > MANIFEST_LIMIT:` | |
| `application/desktop_update/helper.py:335` | `main` | len() against a limit | `or not 0 < len(row[name]) <= 4096` | |
| `application/desktop_update/installed.py:78` | `WindowsRegistration.read` | len() against a limit | `if kind != winreg.REG_SZ or not isinstance(value, str) or len(value) > 4096:` | |
| `application/desktop_update/installed.py:85` | `WindowsRegistration.read` | len() against a limit | `if kind != winreg.REG_SZ or not isinstance(value, str) or len(value) > 4096:` | |
| `application/desktop_update/launch.py:49` | `Installation._from_launch` | len() against a limit | `or not 0 < len(value[name]) <= 4096` | |
| `application/desktop_update/manifest.py:61` | `Version.parse` | len() against a limit | `if not isinstance(value, str) or len(value) > 128:` | |
| `application/desktop_update/manifest.py:191` | `release_url` | len() against a limit | `or len(value) > 2048` | |
| `application/desktop_update/manifest.py:228` | `payload_path` | len() against a limit | `if not isinstance(value, str) or not 0 < len(value) <= 240 or not value.isascii():` | |
| `application/desktop_update/manifest.py:348` | `_parse_manifest` | len() against a limit | `or len(encoded) > 100` | |
| `application/desktop_update/manifest.py:411` | `_parse_manifest` | len() against a limit | `if not isinstance(rows, list) or not 3 <= len(rows) <= 4096:` | |
| `application/desktop_update/recovery_service.py:129` | `_read` | len() against a limit | `if not raw.endswith("\n") or len(raw.encode()) > 65536:` | |
| `application/desktop_update/recovery_service.py:170` | `run_recovery` | len() against a limit | `not isinstance(identity, str) or not 0 < len(identity) <= 128` | |
| `application/desktop_update/replacement.py:129` | `_inventory` | len() against a limit | `if not isinstance(entries, dict) or not 3 <= len(entries) <= 4096:` | |
| `application/desktop_update/replacement.py:253` | `PortableReplacement._restore_receipt` | len() against a limit | `or not 3 <= len(row["old"]) <= 4097` | |
| `application/desktop_update/service.py:109` | `DesktopUpdateService.handle` | len() against a limit | `or len(host["target"]) > 64` | |
| `application/desktop_update/storage.py:278` | `atomic_receipt` | len() against a limit | `if len(raw) > MANIFEST_LIMIT:` | |
| `application/desktop_update/transport.py:239` | `content_length` | len() against a limit | `or len(length) > 20` | |
| `application/desktop_update/transport.py:278` | `metadata_bytes` | len() against a limit | `if len(result) > MANIFEST_LIMIT:` | |
| `interfaces/_display_reads.py:46` | `DisplayReads.cancel` | len() against a limit | `while len(self._cancelled) > 128:` | |
| `interfaces/_local_diagnostics.py:182` | `LocalDiagnostics._load` | len() against a limit | `if len(data) > MAX_SNAPSHOT_BYTES:` | |
| `interfaces/_local_diagnostics.py:298` | `LocalDiagnostics.record` | len() against a limit | `while len(self._records) > MAX_RECORDS:` | |
| `interfaces/_local_diagnostics.py:316` | `LocalDiagnostics._write_snapshot` | len() against a limit | `while len(data) > MAX_SNAPSHOT_BYTES:` | |
| `interfaces/_local_diagnostics.py:354` | `LocalDiagnostics.preview` | len() against a limit | `type(value) is not str or len(value) > MAX_REPORT_TEXT` | |
| `interfaces/_local_diagnostics.py:374` | `LocalDiagnostics.preview` | len() against a limit | `if len(content.encode("utf-8")) > MAX_REPORT_BYTES:` | |
| `interfaces/commands/builder.py:128` | `handle_ingest_builder_sources` | len() against a limit | `or not 1 <= len(raw_paths) <= MAX_SOURCE_FILES` | |
| `interfaces/commands/campaigns.py:270` | `handle_start_guided_campaign` | len() against a limit | `if not isinstance(premise, str) or len(premise) > _MAX_USER_TEXT_CHARS:` | |
| `interfaces/commands/campaigns.py:283` | `handle_start_guided_campaign` | len() against a limit | `or len(raw_companions) > 64` | |
| `interfaces/commands/packages.py:56` | `handle_install_package` | len() against a limit | `if len(archive_bytes) > _MAX_PACKAGE_BYTES:` | |
| `interfaces/commands/providers.py:102` | `handle_admin_full_audit_record` | len() against a limit | `if len(encoded) > _MAX_ADMIN_AUDIT_VIEW_BYTES:` | |
| `interfaces/commands/sessions.py:199` | `handle_steer_session` | len() against a limit | `if len(steering_text) > _MAX_USER_TEXT_CHARS:` | |
| `interfaces/commands/sessions.py:240` | `handle_submit_session_input` | len() against a limit | `if len(user_text) > _MAX_USER_TEXT_CHARS:` | |
| `interfaces/commands/sessions.py:242` | `handle_submit_session_input` | len() against a limit | `if ooc_text is not None and len(ooc_text) > _MAX_USER_TEXT_CHARS:` | |
| `interfaces/commands/sessions.py:393` | `handle_submit_session_input` | len() against a limit | `request_title = len(" ".join(user_text.split())) > TITLE_LIMIT and not any(` | |
| `interfaces/owner_process.py:818` | `_admitted_dm_campaign_plan` | len() against a limit | `if len(admitted_text.encode("utf-8")) > _MAX_CAMPAIGN_PLAN_BYTES:` | |
| `interfaces/owner_process.py:835` | `_read_message` | len() against a limit | `if len(line.encode("utf-8")) > _MAX_MESSAGE_BYTES or not line.endswith("\n"):` | |
| `interfaces/owner_process.py:999` | `_seed_campaign_plan` | len() against a limit | `if not encoded or len(encoded) > _MAX_CAMPAIGN_PLAN_BYTES:` | |
| `interfaces/owner_process.py:1029` | `_seed_campaign_plan` | len() against a limit | `if len(structured_bytes) > _MAX_CAMPAIGN_PLAN_BYTES:` | |
| `interfaces/owner_process.py:2069` | `run` | len() against a limit | `not isinstance(load_id, str) or not 1 <= len(load_id) <= 128` | |
| `interfaces/webui.py:94` | `RemoteAccess.from_file` | len() against a limit | `if port == 443 or len(parsed.hostname or "") > 253:` | |
| `interfaces/webui.py:165` | `OwnerProcess._write` | len() against a limit | `if len(raw) > MAX_BYTES:` | |
| `interfaces/webui.py:182` | `OwnerProcess._read` | len() against a limit | `if not raw or len(raw) > MAX_BYTES:` | |
| `interfaces/webui.py:450` | `LocalWebUI.login` | len() against a limit | `if len(self.remote_sessions) >= 32:` | |
| `interfaces/webui.py:545` | `LocalWebUI.command` | len() against a limit | `if len(self.requests) < 128:` | |
| `interfaces/webui.py:550` | `LocalWebUI.command` | len() against a limit | `if len(self.requests) >= 128:` | |
| `interfaces/webui.py:642` | `LocalWebUI.export` | len() against a limit | `if len(self.downloads) >= 128:` | |
| `interfaces/webui.py:718` | `LocalWebUI.upload` | len() against a limit | `if len(text) > MAX_UPLOAD_PARAMS_BYTES:` | |
| `interfaces/webui.py:736` | `LocalWebUI.upload` | len() against a limit | `or len(name) > 180` | |
| `local_dictation.py:172` | `transcribe` | len() against a limit | `if len(result.encode("utf-8")) > 128_000:` | |
| `local_dictation_process.py:63` | `serve` | len() against a limit | `if not line.endswith("\n") or len(line.encode("utf-8")) > 8192:` | |
| `local_dictation_process.py:117` | `serve_session` | len() against a limit | `if not line.endswith("\n") or len(line.encode("utf-8")) > 8192:` | |
| `local_dictation_process.py:121` | `serve_session` | len() against a limit | `if not isinstance(identity, str) or not 0 < len(identity) <= 64 or not all(` | |
| `release_artifact.py:302` | `_safe_leaf` | len() against a limit | `or len(name) > 180` | |
