# Hash and HMAC call sites

Python snapshot `0da1010feb5a520f917f5d69ff8553ddb174e9e7`. 512 sites. Every `hashlib` and `hmac` call. For each one the port must reproduce the exact bytes fed to the hash: find the encoding in the same function (often a `json.dumps` listed in json-encodings.md) and add a reference vector.

Written by `docs/rust-port/tools/site_lists.py`. A worker ticks the Done column when the Rust side reproduces the site and a vector or comparison session proves it.

| Lane | Sites |
| --- | ---: |
| 1. Formats and content | 132 |
| 2. Game engine | 28 |
| 3. Storage and authority | 214 |
| 4. Providers and agents | 58 |
| 5. Owner, clients and release | 80 |

## Lane 1: Formats and content

| Where | Function | Kind | Code | Done |
| --- | --- | --- | --- | --- |
| `application/builder_references.py:176` | `capture_content_references` | hashlib.sha256 | `suffix = hashlib.sha256(archive).hexdigest()[:24]` |  `sylvia-app` builder_reference_vectors |
| `application/builder_references.py:241` | `capture_content_references` | hashlib.sha256 | `suffix = hashlib.sha256(archive).hexdigest()[:24]` |  `sylvia-app` builder_reference_vectors |
| `application/builder_references.py:353` | `with_character_examples` | hashlib.sha256 | `"content_sha256": hashlib.sha256(canonical_json_bytes(draft)).hexdigest(),` | |
| `application/bundled_packages.py:210` | `_srd_drafts` | hashlib.sha256 | `if item.byte_count != len(value) or item.sha256 != hashlib.sha256(value).hexdigest():` |  `sylvia-app` bundled_vectors |
| `application/bundled_starter_characters.py:89` | `bundled_starter_participants` | hashlib.sha256 | `hashlib.sha256(source.encode()).hexdigest(),` | |
| `application/bundled_starter_characters.py:90` | `bundled_starter_participants` | hashlib.sha256 | `hashlib.sha256(canonical_json_bytes(setting_character_schema or {})).hexdigest(),` |  `sylvia-app` starter_vectors |
| `application/campaign_library_selection.py:319` | `resolve_campaign_plan_selection` | hashlib.sha256 | `install_id=f"plan-snapshot-{hashlib.sha256(snapshot).hexdigest()[:24]}",` |  `sylvia-app` selection_vectors |
| `application/campaign_library_selection.py:422` | `materialize_template_party` | hashlib.sha256 | `participant_namespace=hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24],` |  `sylvia-app` party_vectors |
| `application/campaign_library_selection.py:555` | `materialize_library_party` | hashlib.sha256 | `+ hashlib.sha256(f"{operation_id}\0{artifact_id}".encode()).hexdigest()[:24]` |  `sylvia-app` party_vectors |
| `application/character_card_import.py:168` | `_asset_uri` | hashlib.sha256 | `return "card://default/" + hashlib.sha256(uri.encode()).hexdigest()` | |
| `application/character_card_import.py:171` | `_asset_uri` | hashlib.sha256 | `return "card://inline/" + hashlib.sha256(uri.encode()).hexdigest()` | |
| `application/character_card_import.py:238` | `preview_character_card` | hashlib.sha256 | `digest = hashlib.sha256(source).hexdigest()` | |
| `application/character_card_import.py:462` | `load_import_original` | hashlib.sha256 | `or hashlib.sha256(source).hexdigest() != digest` | |
| `application/character_card_import.py:524` | `_read_pending_preview` | hashlib.sha256 | `if len(source) > MAX_CARD_BYTES or hashlib.sha256(source).hexdigest() != digest:` | |
| `application/character_card_import.py:535` | `_confirmed_card_result` | hashlib.sha256 | `profile_digest = hashlib.sha256(canonical_json_bytes(preview.profile)).hexdigest()` | |
| `application/character_card_import.py:536` | `_confirmed_card_result` | hashlib.sha256 | `operation_hash = hashlib.sha256(("desktop-card:" + digest).encode()).hexdigest()[:24]` | |
| `application/character_profiles.py:231` | `profile_from_snapshot` | hashlib.sha256 | `if hashlib.sha256(canonical_json_bytes(profile)).hexdigest() != digest:` |  `sylvia-app` character_profile_vectors |
| `application/character_profiles.py:335` | `save_character_profile` | hashlib.sha256 | `digest = hashlib.sha256(encoded).hexdigest()` |  `sylvia-app` character_profile_vectors |
| `application/character_profiles.py:336` | `save_character_profile` | hashlib.sha256 | `operation_hash = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]` |  `sylvia-app` character_profile_vectors |
| `application/import_repair.py:124` | `_read_selected_source` | hashlib.sha256 | `return hashlib.sha256(data).hexdigest(), data` | |
| `application/import_repair.py:237` | `inspect_repair_source` | hashlib.sha256 | `_read_safe_zip(snapshot, source_sha256=hashlib.sha256(snapshot).hexdigest())` | |
| `application/import_repair.py:290` | `stage_repair_copy` | hashlib.sha256 | `source_sha256=hashlib.sha256(safe.files[PLAN_SNAPSHOT_FILENAME]).hexdigest(),` | |
| `application/import_repair.py:313` | `stage_repair_copy` | hashlib.sha256 | `snapshot, source_sha256=hashlib.sha256(snapshot).hexdigest()` | |
| `application/import_repair.py:548` | `_plan_result` | hashlib.sha256 | `record["package_snapshot_sha256"] = hashlib.sha256(snapshot).hexdigest()` | |
| `application/import_repair.py:549` | `_plan_result` | hashlib.sha256 | `record["content_sha256"] = hashlib.sha256( canonical_plan + markdown.encode("utf-8") + canonical_contribution + snapshot ).hexdigest()` | |
| `application/import_repair_sessions.py:44` | `offer_import_repair` | hashlib.sha256 | `repair_id = hashlib.sha256( canonical_json_bytes([install_id, inspection.source_sha256, str(source)]) ).hexdigest()` | |
| `application/import_repair_sessions.py:107` | `start_import_repair_chat` | hashlib.sha256 | `+ hashlib.sha256(canonical_json_bytes([operation_id, repair_id])).hexdigest()[:24]` | |
| `application/interview_content.py:100` | `resolve_interview_content` | hashlib.sha256 | `install_id=f"interview-snapshot-{hashlib.sha256(snapshot).hexdigest()[:24]}",` | |
| `application/interview_content.py:129` | `resolve_interview_content` | hashlib.sha256 | `f"{hashlib.sha256(snapshot).hexdigest()[:24]}"` | |
| `application/interview_content.py:141` | `resolve_interview_content` | hashlib.sha256 | `install_id=f"interview-snapshot-{hashlib.sha256(snapshot).hexdigest()[:24]}")` | |
| `application/package_materialization.py:173` | `materialize_package_archive` | hashlib.sha256 | `"sha256": hashlib.sha256(value).hexdigest(),` |  `sylvia-app` bundled_vectors |
| `application/plan_bundle.py:77` | `export_plan_bundle` | hashlib.sha256 | `"archive_sha256": hashlib.sha256(data).hexdigest(),` | |
| `application/ready_campaigns.py:83` | `_entries` | hashlib.sha256 | `"content_sha256": hashlib.sha256( canonical_json_bytes( { "campaign": row, "provenance": payload["provenance"], } ) ).hexdigest(),` | |
| `application/ready_campaigns.py:196` | `resolve_ready_campaign` | hashlib.sha256 | `if hashlib.sha256(source).hexdigest() != member["content_sha256"]:` | |
| `application/ready_campaigns.py:331` | `start_ready_campaign` | hashlib.sha256 | `suffix = hashlib.sha256(operation_id.encode()).hexdigest()[:24]` | |
| `application/ready_campaigns.py:380` | `_start_ready_campaign` | hashlib.sha256 | `suffix = hashlib.sha256(operation_id.encode()).hexdigest()[:24]` | |
| `application/ready_campaigns.py:395` | `_start_ready_campaign` | hashlib.sha256 | `campaign_suffix = hashlib.sha256(campaign_id.encode()).hexdigest()[:24]` | |
| `application/reusable_participants.py:365` | `parse_setting_character` | hashlib.sha256 | `"source_content_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),` |  `sylvia-app` reusable_participant_vectors |
| `application/reusable_participants.py:405` | `parse_setting_character` | hashlib.sha256 | `content_sha256=hashlib.sha256(encoded).hexdigest(),` |  `sylvia-app` reusable_participant_vectors |
| `application/reusable_participants.py:516` | `save_reusable_participant` | hashlib.sha256 | `content_sha256 = hashlib.sha256(canonical_json_bytes(identity)).hexdigest()` |  `sylvia-app` reusable_participant_vectors |
| `application/reusable_participants.py:517` | `save_reusable_participant` | hashlib.sha256 | `operation_suffix = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]` |  `sylvia-app` reusable_participant_vectors |
| `application/reusable_participants.py:727` | `load_reusable_participant` | hashlib.sha256 | `content_sha256 = hashlib.sha256(canonical_json_bytes(identity)).hexdigest()` |  `sylvia-app` reusable_participant_vectors |
| `application/reusable_plans.py:202` | `_save_validated_campaign_plan` | hashlib.sha256 | `content_sha256 = hashlib.sha256( canonical_json_bytes(validated.payload) + validated.readable_markdown.encode("utf-8") + contribution_bytes + (b"" ...` |  `sylvia-app` reusable_plan_vectors |
| `application/reusable_plans.py:208` | `_save_validated_campaign_plan` | hashlib.sha256 | `suffix = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]` |  `sylvia-app` reusable_plan_vectors |
| `application/reusable_plans.py:223` | `_save_validated_campaign_plan` | hashlib.sha256 | `"source_plan_content_sha256": hashlib.sha256( canonical_json_bytes(source_payload) ).hexdigest(),` |  `sylvia-app` reusable_plan_vectors |
| `application/reusable_plans.py:230` | `_save_validated_campaign_plan` | hashlib.sha256 | `record["package_snapshot_sha256"] = hashlib.sha256(package_snapshot).hexdigest()` |  `sylvia-app` reusable_plan_vectors |
| `application/reusable_plans.py:314` | `save_reusable_rift_plan` | hashlib.sha256 | `suffix = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]` |  `sylvia-app` reusable_plan_vectors |
| `application/reusable_plans.py:443` | `load_reusable_campaign_plan` | hashlib.sha256 | `if hashlib.sha256(package_snapshot).hexdigest() != record["package_snapshot_sha256"]:` |  `sylvia-app` reusable_plan_vectors |
| `application/reusable_plans.py:483` | `load_reusable_campaign_plan` | hashlib.sha256 | `content_sha256 = hashlib.sha256( canonical_json_bytes(validated.payload) + validated.readable_markdown.encode("utf-8") + (b"" if contribution is No...` |  `sylvia-app` reusable_plan_vectors |
| `builder/_package_materializer.py:340` | `ruleset_install_id` | hashlib.sha256 | `digest = hashlib.sha256(f"{session_id}\0{closure}".encode()).hexdigest()` |  `sylvia-app` package_materializer_vectors |
| `builder/_ruleset_source.py:613` | `compile_ruleset_source` | hashlib.sha256 | `source_sha = hashlib.sha256(source_bytes).hexdigest()` |  `sylvia-app` ruleset_source_vectors |
| `builder/_setting_materializer.py:36` | `_world_draft` | hashlib.sha256 | `source_sha = hashlib.sha256( canonical_json_bytes( {path: hashlib.sha256(value).hexdigest() for path, value in sorted(files.items())} ) ).hexdigest()` | |
| `builder/_setting_materializer.py:38` | `_world_draft` | hashlib.sha256 | `{path: hashlib.sha256(value).hexdigest() for path, value in sorted(files.items())}` | |
| `builder/_workspace_files.py:38` | `content_version` | hashlib.sha256 | `return hashlib.sha256(content).hexdigest()` | |
| `domain/campaign_plan.py:751` | `validate_campaign_plan` | hashlib.sha256 | `content_sha256 = hashlib.sha256(encoded).hexdigest()` | `sylvia-domain` campaign_plan_vectors |
| `domain/campaign_sequel.py:24` | `digest` | hashlib.sha256 | `return hashlib.sha256(canonical_json_bytes(value)).hexdigest()` | `sylvia-domain` domain_records_vectors |
| `domain/model_capability.py:108` | `ModelCapabilityPlacement.evidence_sha256` | hashlib.sha256 | `return hashlib.sha256(_canonical_bytes(payload)).hexdigest()` | `sylvia-domain` session_vectors |
| `domain/model_capability.py:323` | `model_guidance_payload_sha256` | hashlib.sha256 | `return hashlib.sha256(_canonical_bytes(model_guidance_payload(decision))).hexdigest()` | `sylvia-domain` session_vectors |
| `domain/package_library.py:600` | `participant_schema_sha256` | hashlib.sha256 | `return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()` | `sylvia-domain` identity_vectors |
| `domain/package_library.py:718` | `content_root_for_record` | hashlib.sha256 | `return hashlib.sha256(canonical_json_bytes(without_claim)).hexdigest()` | `sylvia-domain` identity_vectors |
| `domain/package_library.py:732` | `closure_sha256` | hashlib.sha256 | `return hashlib.sha256(canonical_json_bytes(membership)).hexdigest()` | `sylvia-domain` identity_vectors |
| `domain/package_library.py:738` | `activation_payload_sha256` | hashlib.sha256 | `return hashlib.sha256( canonical_json_bytes( { "campaign_id": command.campaign_id, "candidate_id": command.candidate_id, "expected_revision": comma...` | `sylvia-domain` identity_vectors |
| `domain/session.py:146` | `builder_dm_handoff_payload_sha256` | hashlib.sha256 | `return hashlib.sha256(encoded).hexdigest()` | `sylvia-domain` session_vectors |
| `domain/session.py:229` | `campaign_plan_payload_sha256` | hashlib.sha256 | `return hashlib.sha256(encoded).hexdigest()` | `sylvia-domain` session_vectors |
| `domain/session.py:311` | `agent_session_payload_sha256` | hashlib.sha256 | `return hashlib.sha256(encoded).hexdigest()` | `sylvia-domain` session_vectors |
| `domain/session.py:499` | `builder_chat_payload_sha256` | hashlib.sha256 | `return hashlib.sha256(encoded).hexdigest()` | `sylvia-domain` session_vectors |

## Lane 2: Game engine

| Where | Function | Kind | Code | Done |
| --- | --- | --- | --- | --- |
| `application/_runtime_cache.py:34` | `cached_runtime` | hashlib.sha256 | `"runtime_sha256": hashlib.sha256(runtime_bytes).hexdigest(),` | |
| `application/_runtime_cache.py:37` | `cached_runtime` | hashlib.sha256 | `key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()` | |
| `application/_runtime_cache.py:49` | `cached_runtime` | hashlib.sha256 | `and document["payload_sha256"] == hashlib.sha256(encoded).hexdigest()` | |
| `application/_runtime_cache.py:63` | `cached_runtime` | hashlib.sha256 | `"payload_sha256": hashlib.sha256(encoded).hexdigest(),` | |
| `application/action_sequences.py:115` | `_consent` | hashlib.sha256 | `return hashlib.sha256( canonical_json_bytes( { "sequence_id": queue["sequence_id"], "version": queue["version"], "steps": [ {k: s.get(k) for k in (...` | |
| `application/action_sequences.py:507` | `compose` | hashlib.sha256 | `+ hashlib.sha256( canonical_json_bytes( { "sequence": sequence_id, "step": step_id, "edit": expected_version + 1, "session": session_id, "source": ...` | |
| `application/actor_decisions.py:190` | `project_actor_decision` | hashlib.sha256 | `return replace(request, decision_id=hashlib.sha256(identity).hexdigest())` | |
| `application/package_runtime.py:432` | `AuthorityMemoryStateBridge._record_spatial_evidence` | hashlib.sha256 | `"ordered_result_sha256": hashlib.sha256(result_bytes).hexdigest(),` | |
| `application/package_runtime.py:504` | `AuthorityMemoryStateBridge.spatial_query` | hashlib.sha256 | `identity = 'circle_overlap:' + hashlib.sha256(canonical_json_bytes(facts)).hexdigest()` | |
| `application/package_runtime.py:1503` | `materialize_package_mechanic.mechanic` | hashlib.sha256 | `seed = int.from_bytes(hashlib.sha256(seed_basis).digest()[:8], "big")` | |
| `spatial/_interface.py:913` | `admit_spatial_scene` | hashlib.sha256 | `snapshot_id = hashlib.sha256( canonical_json_bytes({"algorithm_version": SPATIAL_ALGORITHM_VERSION, "scene": payload}) ).hexdigest()` |  `sylvia-engine` spatial_scene_vectors |
| `spatial/_interface.py:925` | `_scene_sha256` | hashlib.sha256 | `return hashlib.sha256(canonical_json_bytes(scene.as_payload())).hexdigest()` |  `sylvia-engine` spatial_mutation_vectors |
| `spatial/_interface.py:1002` | `mutate_spatial_scene` | hashlib.sha256 | `"target_sha256": hashlib.sha256(canonical_json_bytes(before_target)).hexdigest(),` |  `sylvia-engine` spatial_mutation_vectors |
| `spatial/_interface.py:1008` | `mutate_spatial_scene` | hashlib.sha256 | `"target_sha256": hashlib.sha256(canonical_json_bytes(after_target)).hexdigest(),` |  `sylvia-engine` spatial_mutation_vectors |

## Lane 3: Storage and authority

| Where | Function | Kind | Code | Done |
| --- | --- | --- | --- | --- |
| `application/authority.py:1724` | `AuthorityOwner.update_campaign_file` | hashlib.sha256 | `request_hash=hashlib.sha256(canonical_json_bytes({ "updates": list(all_updates), "session_id": session_id, "basis_message_ids": list(basis_message_...` | |
| `application/authority.py:1771` | `AuthorityOwner.update_campaign_file` | hashlib.sha256 | `action_id="character-" + hashlib.sha256(canonical_json_bytes({ "update_id": update_id, "session_id": session_id, "basis_message_ids": list(basis_me...` | |
| `application/authority.py:2788` | `AuthorityOwner.admit_campaign_plan` | hashlib.sha256 | `actual_content_sha256 = hashlib.sha256( canonical_json_bytes(captured_content) ).hexdigest()` | |
| `application/authority.py:3259` | `AuthorityOwner.confirm_new_campaign` | hashlib.sha256 | `payload_sha256 = hashlib.sha256(canonical_json_bytes(semantic_payload)).hexdigest()` | |
| `application/authority.py:3327` | `AuthorityOwner.create_guided_campaign_draft` | hashlib.sha256 | `"campaign-draft-" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]` | |
| `application/authority.py:3521` | `AuthorityOwner._next_guided_question` | hashlib.sha256 | `expected = hashlib.sha256( canonical_json_bytes(contribution.as_payload()), ).hexdigest()` | |
| `application/authority.py:3629` | `AuthorityOwner.advance_guided_campaign_draft` | hashlib.sha256 | `"contribution_sha256": hashlib.sha256( canonical_json_bytes(contribution.as_payload()), ).hexdigest(),` | |
| `application/authority.py:3738` | `AuthorityOwner.bind_guided_campaign_basis` | hashlib.sha256 | `payload_sha256 = hashlib.sha256(canonical_json_bytes(semantic_payload)).hexdigest()` | |
| `application/authority.py:4095` | `AuthorityOwner.begin_campaign_construction` | hashlib.sha256 | `payload_sha256 = hashlib.sha256(canonical_json_bytes(semantic_payload)).hexdigest()` | |
| `application/authority.py:4152` | `AuthorityOwner.complete_campaign_construction` | hashlib.sha256 | `payload_sha256 = hashlib.sha256(canonical_json_bytes(semantic_payload)).hexdigest()` | |
| `application/authority.py:4264` | `AuthorityOwner.finalize_campaign_construction` | hashlib.sha256 | `"artifact_content_sha256": hashlib.sha256( canonical_json_bytes(captured_plan) ).hexdigest(),` | |
| `application/authority.py:4273` | `AuthorityOwner.finalize_campaign_construction` | hashlib.sha256 | `content_sha256 = hashlib.sha256(canonical_json_bytes(plan_content)).hexdigest()` | |
| `application/authority.py:4274` | `AuthorityOwner.finalize_campaign_construction` | hashlib.sha256 | `suffix = hashlib.sha256(campaign_id.encode("utf-8")).hexdigest()[:24]` | |
| `application/authority.py:4308` | `AuthorityOwner.finalize_campaign_construction` | hashlib.sha256 | `completion_payload_sha256 = hashlib.sha256( canonical_json_bytes(completion_payload) ).hexdigest()` | |
| `application/authority.py:5433` | `AuthorityOwner.package_candidate_text` | hashlib.sha256 | `"sha256": hashlib.sha256(value).hexdigest(),` | |
| `application/authority.py:5479` | `AuthorityOwner.export_package_candidate` | hashlib.sha256 | `digest = hashlib.sha256()` | |
| `application/authority.py:6044` | `AuthorityOwner.read_campaign_reference` | hashlib.sha256 | `"content_sha256": hashlib.sha256(content_bytes).hexdigest(),` | |
| `application/authority.py:7379` | `AuthorityOwner._player_action_in_owned_work` | hashlib.sha256 | `confirmation_token = hashlib.sha256(canonical_json_bytes({ "operation_id": operation_id, "origin": origin, "parameters": confirmation_parameters, "...` | |
| `application/authority.py:8098` | `AuthorityOwner.purge_campaign` | hashlib.sha256 | `payload_sha256 = hashlib.sha256(canonical_json_bytes(semantic_payload)).hexdigest()` | `sylvia-app` purge |
| `application/authority.py:8173` | `AuthorityOwner.campaign_purge_receipt_exists` | hashlib.sha256 | `payload_sha256 = hashlib.sha256(canonical_json_bytes(semantic_payload)).hexdigest()` | `sylvia-app` purge (reconcile) |
| `application/authority.py:8224` | `AuthorityOwner._lifecycle_operation` | hashlib.sha256 | `payload_sha256 = hashlib.sha256(canonical_json_bytes(semantic_payload)).hexdigest()` | `sylvia-app` lifecycle |
| `application/authority.py:9922` | `_semantic_hash` | hashlib.sha256 | `return hashlib.sha256(encoded.encode("utf-8")).hexdigest()` | |
| `application/campaign_rules_update.py:103` | `_offer_bundled_update` | hashlib.sha256 | `install_id="bundled-update-" + hashlib.sha256(raw).hexdigest(),` | |
| `application/campaign_rules_update.py:109` | `_offer_bundled_update` | hashlib.sha256 | `+ hashlib.sha256( f"{current.closure_sha256}:{bundled.closure_sha256}".encode("ascii") ).hexdigest()` | |
| `application/campaign_rules_update.py:767` | `_preview` | hashlib.sha256 | `result["confirmation"] = hashlib.sha256(canonical_json_bytes(result)).hexdigest()` | |
| `infrastructure/builder_source_ingestion.py:159` | `_source_identity` | hashlib.sha256 | `content_sha256 = hashlib.sha256(payload).hexdigest()` | |
| `infrastructure/builder_source_ingestion.py:160` | `_source_identity` | hashlib.sha256 | `identity = hashlib.sha256( media_type.encode("utf-8") + b"\x00" + bytes.fromhex(content_sha256) ).hexdigest()[:24]` | |
| `infrastructure/builder_source_ingestion.py:218` | `ingest_builder_sources` | hashlib.sha256 | `batch_identity = hashlib.sha256( b"\x00".join( item[2].encode("ascii") for item in sorted(extracted_sources, key=lambda row: row[0]) ) ).hexdigest(...` | |
| `infrastructure/builder_source_ingestion.py:225` | `ingest_builder_sources` | hashlib.sha256 | `batch_identity = hashlib.sha256(batch_id.encode("utf-8")).hexdigest()[:24]` | |
| `infrastructure/builder_source_ingestion.py:329` | `_purge_operation_name` | hashlib.sha256 | `return "purge-" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]` | `sylvia-app` purge |
| `infrastructure/campaign_archive.py:724` | `source_authority_schema_claim_sha256` | hashlib.sha256 | `return hashlib.sha256(_json_bytes(preimage)).hexdigest()` | `sylvia-store` campaign_archive |
| `infrastructure/campaign_archive.py:1550` | `_validate_manifest_derived_package_graph.closure_digest` | hashlib.sha256 | `return hashlib.sha256(canonical_json_bytes(representation)).hexdigest()` | not needed: `_validate_manifest_derived_package_graph` has no product caller (retired) |
| `infrastructure/campaign_archive.py:1696` | `_stream_member_digest` | hashlib.sha256 | `digest = hashlib.sha256()` | `sylvia-store` archive_validation |
| `infrastructure/campaign_archive.py:1830` | `_AuthorityScanSummary` | hashlib.sha256 | `default_factory=lambda: {table: hashlib.sha256() for table in _PACKAGE_RELATION_TABLES}` | `sylvia-store` archive_validation |
| `infrastructure/campaign_archive.py:1833` | `_AuthorityScanSummary` | hashlib.sha256 | `default_factory=lambda: {table: hashlib.sha256() for table in _PACKAGE_RELATION_TABLES}` | `sylvia-store` archive_validation |
| `infrastructure/campaign_archive.py:2050` | `_record_manifest_summary` | hashlib.sha256 | `canonical_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),` | `sylvia-store` archive_validation |
| `infrastructure/campaign_archive.py:2224` | `_record_authority_summary` | hashlib.sha256 | `hashlib.sha256(report_json.encode("utf-8")).hexdigest(),` | `sylvia-store` archive_validation |
| `infrastructure/campaign_archive.py:2267` | `_record_authority_summary` | hashlib.sha256 | `hashlib.sha256(canonical_json_bytes(package_record)).hexdigest(),` | `sylvia-store` archive_validation |
| `infrastructure/campaign_archive.py:2272` | `_record_authority_summary` | hashlib.sha256 | `report_sha256=hashlib.sha256(report_json.encode("utf-8")).hexdigest(),` | `sylvia-store` archive_validation |
| `infrastructure/campaign_archive.py:2325` | `_scan_authority_member` | hashlib.sha256 | `digest = hashlib.sha256()` | `sylvia-store` archive_validation |
| `infrastructure/campaign_archive.py:2471` | `_validate_streamed_authority.streamed_closure_digest` | hashlib.sha256 | `return hashlib.sha256(canonical_json_bytes(representation)).hexdigest()` | `sylvia-store` archive_validation |
| `infrastructure/campaign_archive.py:3095` | `stage_campaign_archive` | hashlib.sha256 | `digest = hashlib.sha256()` | |
| `infrastructure/launch_handshake.py:84` | `LaunchHandshake.issue` | hmac.new | `response = hmac.new(secret, _HANDSHAKE_TRANSCRIPT, hashlib.sha256).hexdigest()` | yes |
| `infrastructure/launch_handshake.py:107` | `LaunchHandshake.verify` | hmac.compare_digest | `if not hmac.compare_digest( hmac.new(sealed.secret, _HANDSHAKE_TRANSCRIPT, hashlib.sha256).hexdigest(), response_hex, ):` | yes |
| `infrastructure/launch_handshake.py:108` | `LaunchHandshake.verify` | hmac.new | `hmac.new(sealed.secret, _HANDSHAKE_TRANSCRIPT, hashlib.sha256).hexdigest(),` | yes |
| `infrastructure/launch_handshake.py:138` | `derive_owner_response` | hmac.new | `return hmac.new(secret, _HANDSHAKE_TRANSCRIPT, hashlib.sha256).hexdigest()` | yes |
| `infrastructure/package_archive.py:238` | `admit_package_archive` | hashlib.sha256 | `digest = hashlib.sha256()` | `sylvia-domain` archive_vectors |
| `infrastructure/sqlite_authority.py:509` | `<module>` | hashlib.sha256 | `_MIGRATION_1_SHA256 = hashlib.sha256(_MIGRATION_1.strip().encode("utf-8")).hexdigest()` | yes |
| `infrastructure/sqlite_authority.py:587` | `<module>` | hashlib.sha256 | `_MIGRATION_2_SHA256 = hashlib.sha256(_MIGRATION_2.strip().encode("utf-8")).hexdigest()` | yes |
| `infrastructure/sqlite_authority.py:590` | `<module>` | hashlib.sha256 | `_MIGRATION_3_SHA256 = hashlib.sha256(_MIGRATION_3.strip().encode("utf-8")).hexdigest()` | yes |
| `infrastructure/sqlite_authority.py:593` | `<module>` | hashlib.sha256 | `_MIGRATION_4_SHA256 = hashlib.sha256(_MIGRATION_4.strip().encode("utf-8")).hexdigest()` | yes |
| `infrastructure/sqlite_authority.py:598` | `<module>` | hashlib.sha256 | `_MIGRATION_5_SHA256 = hashlib.sha256(_MIGRATION_5.strip().encode("utf-8")).hexdigest()` | yes |
| `infrastructure/sqlite_authority.py:607` | `<module>` | hashlib.sha256 | `_MIGRATION_8_SHA256 = hashlib.sha256(_MIGRATION_8.strip().encode("utf-8")).hexdigest()` | yes |
| `infrastructure/sqlite_authority.py:944` | `_stored_action_hash` | hashlib.sha256 | `return hashlib.sha256(encoded.encode("utf-8")).hexdigest()` | |
| `infrastructure/sqlite_authority.py:1493` | `SQLiteAuthorityStore.export_campaign` | hashlib.sha256 | `"sha256": hashlib.sha256(capture.authority_bytes).hexdigest(),` | `sylvia-store` export_writer |
| `infrastructure/sqlite_authority.py:1513` | `SQLiteAuthorityStore.export_campaign` | hashlib.sha256 | `"sha256": hashlib.sha256(capture.private_bytes).hexdigest(),` | `sylvia-store` export_writer |
| `infrastructure/sqlite_authority.py:1586` | `SQLiteAuthorityStore.export_campaign` | hashlib.sha256 | `digest = hashlib.sha256()` | `sylvia-store` export_writer |
| `infrastructure/sqlite_authority.py:1715` | `SQLiteAuthorityStore._write_export_blob` | hashlib.sha256 | `digest = hashlib.sha256()` | `sylvia-store` export_writer |
| `infrastructure/sqlite_authority.py:1738` | `SQLiteAuthorityStore._write_export_blob` | hashlib.sha256 | `or str(row["chunk_sha256"]) != hashlib.sha256(data).hexdigest()` | `sylvia-store` export_writer |
| `infrastructure/sqlite_authority.py:4165` | `SQLiteAuthorityStore._verify_construction_authority` | hashlib.sha256 | `or hashlib.sha256(canonical_json_bytes(payload)).hexdigest()` | |
| `infrastructure/sqlite_authority.py:4460` | `SQLiteAuthorityStore._verify_construction_authority` | hashlib.sha256 | `representation_sha256 = hashlib.sha256( canonical_json_bytes( { "canonical_input": canonical_input, "canonical_plan": canonical_plan, } ) ).hexdige...` | |
| `infrastructure/sqlite_authority.py:5675` | `SQLiteAuthorityStore._verify_session_identities` | hashlib.sha256 | `if hashlib.sha256(_dump(plan.content).encode("utf-8")).hexdigest() != (` | |
| `infrastructure/sqlite_authority.py:7452` | `SQLiteAuthorityStore.commit_campaign_continuity_batch` | hashlib.sha256 | `hashlib.sha256(canonical_json_bytes(semantic_payload)).hexdigest(),` | |
| `infrastructure/sqlite_authority.py:8457` | `SQLiteAuthorityStore.create_guided_campaign_draft` | hashlib.sha256 | `suffix = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]` | |
| `infrastructure/sqlite_authority.py:8620` | `SQLiteAuthorityStore.advance_guided_campaign_draft` | hashlib.sha256 | `suffix = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]` | |
| `infrastructure/sqlite_authority.py:8864` | `SQLiteAuthorityStore.start_guided_creation_subflow` | hashlib.sha256 | `suffix = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]` | |
| `infrastructure/sqlite_authority.py:9050` | `SQLiteAuthorityStore.return_guided_creation_subflow` | hashlib.sha256 | `suffix = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]` | |
| `infrastructure/sqlite_authority.py:9821` | `SQLiteAuthorityStore.complete_campaign_construction` | hashlib.sha256 | `representation_sha256 = hashlib.sha256( canonical_json_bytes( {"canonical_input": canonical_input, "canonical_plan": canonical_plan} ) ).hexdigest()` | |
| `infrastructure/sqlite_authority.py:10035` | `SQLiteAuthorityStore.commit_guided_campaign_start` | hashlib.sha256 | `representation_sha256 = hashlib.sha256( canonical_json_bytes( {"canonical_input": canonical_input, "canonical_plan": canonical_plan} ) ).hexdigest()` | |
| `infrastructure/sqlite_authority.py:10551` | `SQLiteAuthorityStore._pending_from_row` | hashlib.sha256 | `descriptor_sha256 = hashlib.sha256(str(row["descriptor_json"]).encode("utf-8")).hexdigest()` | |
| `infrastructure/sqlite_authority.py:10732` | `SQLiteAuthorityStore.mechanics_choice_resolution_receipt` | hashlib.sha256 | `!= hashlib.sha256(_dump(resolution_payload).encode("utf-8")).hexdigest()` | |
| `infrastructure/sqlite_authority.py:10781` | `SQLiteAuthorityStore.reserve_mechanics_choice` | hashlib.sha256 | `descriptor_sha256 = hashlib.sha256(descriptor_json.encode("utf-8")).hexdigest()` | |
| `infrastructure/sqlite_authority.py:11222` | `SQLiteAuthorityStore.commit_action` | hashlib.sha256 | `resolution_sha256 = hashlib.sha256( _dump(resolution_payload).encode("utf-8") ).hexdigest()` | |
| `infrastructure/sqlite_authority.py:11232` | `SQLiteAuthorityStore.commit_action` | hashlib.sha256 | `!= hashlib.sha256( _dump( owned_payload( { "choice_id": choice_resolution.expected_request.choice_id, "prompt": choice_resolution.expected_request....` | |
| `infrastructure/sqlite_authority.py:11567` | `SQLiteAuthorityStore.commit_action` | hashlib.sha256 | `hashlib.sha256( _dump( owned_payload( { "choice_id": choice_resolution.expected_request.choice_id, "prompt": choice_resolution.expected_request.pro...` | |
| `infrastructure/sqlite_authority.py:12711` | `SQLiteAuthorityStore.commit_participant_adaptation` | hashlib.sha256 | `source_data_sha256 = hashlib.sha256(canonical_json_bytes(source_data)).hexdigest()` | |
| `infrastructure/sqlite_authority.py:12855` | `SQLiteAuthorityStore.consume_package_blob` | hashlib.sha256 | `digest = hashlib.sha256()` | |
| `infrastructure/sqlite_authority.py:12866` | `SQLiteAuthorityStore.consume_package_blob` | hashlib.sha256 | `or str(chunk_row["chunk_sha256"]) != hashlib.sha256(data).hexdigest()` | |
| `infrastructure/sqlite_authority.py:12935` | `SQLiteAuthorityStore.add_package_recovery_root` | hashlib.sha256 | `if hashlib.sha256(package_root).hexdigest() != blob_sha256:` | |
| `infrastructure/sqlite_authority.py:12952` | `SQLiteAuthorityStore.add_package_recovery_root` | hashlib.sha256 | `hashlib.sha256(chunk).hexdigest(),` | |
| `infrastructure/sqlite_authority.py:13445` | `SQLiteAuthorityStore.advance_mechanics_choice` | hashlib.sha256 | `digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()` | |
| `infrastructure/sqlite_builder_launch.py:148` | `detach_legacy_chat` | hashlib.sha256 | `suffix = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:24]` | |
| `infrastructure/sqlite_builder_launch.py:200` | `launch_from_interview` | hashlib.sha256 | `suffix = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]` | |
| `infrastructure/sqlite_campaign.py:152` | `<module>` | hashlib.sha256 | `CAMPAIGN_IDENTITY_DELTA_SHA256 = hashlib.sha256( CAMPAIGN_IDENTITY_DELTA_SQL.strip().encode("utf-8") ).hexdigest()` | yes |
| `infrastructure/sqlite_campaign.py:214` | `<module>` | hashlib.sha256 | `LIFECYCLE_EXTENSION_SHA256 = hashlib.sha256(LIFECYCLE_EXTENSION_SQL.encode("utf-8")).hexdigest()` | yes |
| `infrastructure/sqlite_campaign.py:259` | `<module>` | hashlib.sha256 | `PURGE_EXTENSION_SHA256 = hashlib.sha256(PURGE_EXTENSION_SQL.encode("utf-8")).hexdigest()` | yes |
| `infrastructure/sqlite_campaign.py:361` | `adaptation_payload_sha256` | hashlib.sha256 | `return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()` | `sylvia-store` stored_authority (`cast`) |
| `infrastructure/sqlite_campaign.py:589` | `verify_f5_campaign_authority` | hashlib.sha256 | `hashlib.sha256(canonical_json_bytes(source_data)).hexdigest(),` | not needed: Python never compares this tuple member |
| `infrastructure/sqlite_campaign.py:626` | `verify_f5_campaign_authority` | hashlib.sha256 | `hashlib.sha256(canonical_json_bytes(value)).hexdigest()` | `sylvia-store` stored_authority (`cast`) |
| `infrastructure/sqlite_continuity.py:45` | `<module>` | hashlib.sha256 | `MIGRATION_9_SHA256 = hashlib.sha256(MIGRATION_9_SQL.strip().encode("utf-8")).hexdigest()` | yes |
| `infrastructure/sqlite_continuity.py:111` | `_initial_entry_id` | hashlib.sha256 | `digest = hashlib.sha256(f"{campaign_id}\0{kind}\0{record_id}".encode()).hexdigest()[:32]` | |
| `infrastructure/sqlite_continuity.py:266` | `_backfill_legacy_messages` | hashlib.sha256 | `entry_id = f"legacy-{hashlib.sha256(legacy_key.encode()).hexdigest()[:32]}"` | |
| `infrastructure/sqlite_draft_lifecycle.py:93` | `<module>` | hashlib.sha256 | `MIGRATION_10_SHA256 = hashlib.sha256(MIGRATION_10_SQL.strip().encode("utf-8")).hexdigest()` | yes |
| `infrastructure/sqlite_effort.py:23` | `<module>` | hashlib.sha256 | `MIGRATION_12_SHA256 = hashlib.sha256(MIGRATION_12_SQL.encode("utf-8")).hexdigest()` | yes |
| `infrastructure/sqlite_interview_navigation.py:38` | `navigate` | hashlib.sha256 | `suffix = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]` | |
| `infrastructure/sqlite_mechanics_choice.py:60` | `<module>` | hashlib.sha256 | `MIGRATION_7_SHA256 = hashlib.sha256(MIGRATION_7_SQL.strip().encode("utf-8")).hexdigest()` | yes |
| `infrastructure/sqlite_packages.py:278` | `_sha` | hashlib.sha256 | `return hashlib.sha256(value).hexdigest()` | |
| `infrastructure/sqlite_packages.py:282` | `_legacy_lock_id` | hashlib.sha256 | `return "legacy-lock-" + hashlib.sha256(campaign_id.encode("utf-8")).hexdigest()[:32]` | |
| `infrastructure/sqlite_packages.py:1637` | `verify_package_authority` | hashlib.sha256 | `digest = staged_sha256s.setdefault(file_order, hashlib.sha256())` | |
| `infrastructure/sqlite_reasoning.py:39` | `<module>` | hashlib.sha256 | `MIGRATION_11_SHA256 = hashlib.sha256(MIGRATION_11_SQL.encode("utf-8")).hexdigest()` | yes |
| `infrastructure/sqlite_recovery.py:35` | `<module>` | hashlib.sha256 | `RECOVERY_EXTENSION_SHA256 = hashlib.sha256( RECOVERY_EXTENSION_SQL.strip().encode("utf-8") ).hexdigest()` | yes |
| `infrastructure/sqlite_recovery.py:252` | `semantic_payload` | hashlib.sha256 | `return encoded.decode("utf-8"), hashlib.sha256(encoded).hexdigest()` | |
| `infrastructure/sqlite_recovery.py:350` | `verify_recovery_receipts` | hashlib.sha256 | `or hashlib.sha256(payload_json.encode("utf-8")).hexdigest() != payload_sha256` | `sylvia-store` stored_authority (`recovered`) |
| `infrastructure/sqlite_recovery.py:491` | `_blob_chunk_rows` | hashlib.sha256 | `"chunk_sha256": hashlib.sha256(chunk).hexdigest(),` | |
| `infrastructure/sqlite_recovery.py:543` | `_staging_chunk_rows` | hashlib.sha256 | `"chunk_sha256": hashlib.sha256(chunk).hexdigest(),` | |
| `infrastructure/sqlite_session.py:908` | `rename_builder_chat` | hashlib.sha256 | `message_id="builder-title-user-" + hashlib.sha256(session_id.encode()).hexdigest()[:24],` | |
| `infrastructure/sqlite_state_ownership.py:106` | `<module>` | hashlib.sha256 | `MIGRATION_6_SHA256 = hashlib.sha256(MIGRATION_6_SQL.strip().encode("utf-8")).hexdigest()` | yes |

## Lane 4: Providers and agents

| Where | Function | Kind | Code | Done |
| --- | --- | --- | --- | --- |
| `agent/_actor_decisions.py:60` | `run_actor_decision` | hashlib.sha256 | `context_id = hashlib.sha256(_json(identity).encode()).hexdigest()` | |
| `agent/_builder_session.py:613` | `_builder_prompt` | hashlib.sha256 | `skill_sha256 = hashlib.sha256(loaded_skill.text.encode("utf-8")).hexdigest()` | |
| `agent/_builder_session.py:1298` | `_builder_skill_loader_tool.load` | hashlib.sha256 | `"sha256": hashlib.sha256(loaded.text.encode("utf-8")).hexdigest(),` | |
| `agent/_builder_session.py:1664` | `_campaign_plan_tool` | hashlib.sha256 | `skill_sha256 = hashlib.sha256(skill.text.encode("utf-8")).hexdigest()` | `sylvia-agent` plan_tool_vectors (the tracked skill digest) |
| `agent/_builder_session.py:1854` | `_campaign_plan_draft_tool.create` | hashlib.sha256 | `plan_suffix = hashlib.sha256( canonical_json_bytes({"premise": premise, "title": title}) ).hexdigest()[:16]` | `sylvia-agent` plan_tool_vectors (the draft's Plan id) |
| `agent/_content_definition_tool.py:116` | `content_definition_tool.read` | hashlib.sha256 | `"definition_sha256": hashlib.sha256(canonical_json_bytes(definition)).hexdigest()}` | `sylvia-agent` character_tool_vectors (content_definition sections) |
| `agent/_context_ledger.py:436` | `host_seal` | hashlib.sha256 | `digest = hashlib.sha256(b"sylvia-gm-host-record-seal/v1\x00" + session_id.encode("utf-8"))` | |
| `agent/_context_ledger.py:491` | `_sha` | hashlib.sha256 | `return hashlib.sha256(text.encode("utf-8")).hexdigest()` | |
| `agent/_inference_identity.py:18` | `inference_context_id` | hashlib.sha256 | `return "sylvia-" + hashlib.sha256(encoded).hexdigest()` | |
| `agent/_provider_session.py:417` | `_package_tool_alias` | hashlib.sha256 | `digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]` | `sylvia-agent` tool_projection_vectors |
| `agent/_provider_turn_phases.py:387` | `retry_repeated_response` | hashlib.sha256 | `"response_sha256": hashlib.sha256(state.narration.encode("utf-8")).hexdigest(),` | |
| `agent/_stateless_mcp.py:2359` | `StatelessMcpMechanics.commit_campaign_file` | hashlib.sha256 | `update_id = f"campaign-file-{hashlib.sha256(identity_payload).hexdigest()[:32]}"` || `sylvia-agent` campaign_file_vectors (the update id) |
| `agent/_stateless_mcp.py:2497` | `StatelessMcpMechanics.commit_narrative_state` | hashlib.sha256 | `update_id = f"narrative-{hashlib.sha256(identity_payload).hexdigest()[:32]}"` || not ported: `commit_narrative_state` has no caller (T3m-3) |
| `agent/_working_context.py:89` | `durable_tool_result` | hashlib.sha256 | `result["content_sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()` | `sylvia-agent` working_context_vectors |
| `agent/_working_context.py:177` | `_groups` | hashlib.sha256 | `groups.append(_Group(bundle, [hashlib.sha256(k.encode()).hexdigest() for k in keys]))` | `sylvia-agent` working_context_vectors |
| `application/character_chat.py:398` | `create_chat` | hashlib.sha256 | `chat_id = "character-chat-" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]` | |
| `application/character_chat.py:636` | `send_message` | hashlib.sha256 | `operation = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()` | |
| `config/provider_settings.py:424` | `_provider_profile_id` | hashlib.sha256 | `digest = hashlib.sha256(f"{display_name.casefold()}\n{base_url}".encode()).hexdigest()[:16]` | |
| `provider/_admin_audit.py:94` | `_serialization_projection` | hashlib.sha256 | `"sha256": hashlib.sha256(encoded).hexdigest(),` | `sylvia-provider` admin_audit_vectors |
| `provider/_claude_install.py:350` | `ClaudeInstaller._install` | hashlib.sha256 | `digest = hashlib.sha256()` | `sylvia-provider` install_vectors |
| `provider/_context_fit.py:160` | `ContextFittingProvider._admit` | hashlib.sha256 | `request_sha256=hashlib.sha256(b"").hexdigest(),` | `sylvia-provider` context_fit_vectors |
| `provider/_context_fit.py:270` | `ContextFittingProvider._admit` | hashlib.sha256 | `request_sha256=hashlib.sha256(request_bytes).hexdigest(),` | `sylvia-provider` context_fit_vectors |
| `provider/_request_metadata.py:33` | `opencode_request_headers` | hashlib.sha256 | `digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()` | |
| `session/_prompt_assembler.py:232` | `PromptAssembler.__init__` | hashlib.sha256 | `self._stable_base_hash = hashlib.sha256(self._stable_base.encode()).hexdigest()` | |
| `session/_prompt_assembler.py:456` | `PromptAssembler.build_character` | hashlib.sha256 | `current_hash = hashlib.sha256(state_payload.encode()).hexdigest()` | |
| `session/_prompt_assembler.py:866` | `PromptAssembler.assemble` | hashlib.sha256 | `context_hash = hashlib.sha256(context_hash_input.encode()).hexdigest()` | |
| `session/_prompt_assembler.py:1027` | `PromptAssembler._get_tool_suffix` | hashlib.sha256 | `suffix_hash = hashlib.sha256(suffix.encode()).hexdigest()` | |
| `session/_prompt_assembler.py:1044` | `PromptAssembler._assert_stable_unchanged` | hashlib.sha256 | `current_hash = hashlib.sha256(base_text.encode()).hexdigest()` | |
| `session/_prompt_assembler.py:1065` | `PromptAssembler._build_context_cached` | hashlib.sha256 | `context_hash = hashlib.sha256(context_hash_input.encode()).hexdigest()` | |

## Lane 5: Owner, clients and release

| Where | Function | Kind | Code | Done |
| --- | --- | --- | --- | --- |
| `application/desktop_update/checkpoint.py:78` | `_fingerprint` | hashlib.sha256 | `digest, count = hashlib.sha256(), 0` | `preparation_vectors` |
| `application/desktop_update/checkpoint.py:142` | `schema_identity` | hashlib.sha256 | `return version, hashlib.sha256(raw).hexdigest()` | `preparation_vectors` |
| `application/desktop_update/payload.py:28` | `verify_artifact` | hashlib.sha256 | `digest = hashlib.sha256()` | `update_vectors` |
| `application/desktop_update/payload.py:102` | `extract_portable` | hashlib.sha256 | `digest, count = hashlib.sha256(), 0` | `preparation_vectors` |
| `application/desktop_update/preparation.py:138` | `PreparedUpdate._checkpoint_binding` | hashlib.sha256 | `"sha256": hashlib.sha256(raw).hexdigest(),` | `preparation_vectors` |
| `application/desktop_update/replacement.py:80` | `file_matches` | hashlib.sha256 | `digest, count = hashlib.sha256(), 0` | `preparation_vectors` |
| `application/desktop_update/replacement.py:156` | `_inventory` | hashlib.sha256 | `files.append(PayloadFile(ENTRY_MANIFEST, len(raw), hashlib.sha256(raw).hexdigest()))` | `preparation_vectors` |
| `application/desktop_update/transport.py:327` | `download_artifact` | hashlib.sha256 | `digest, count = hashlib.sha256(), 0` | `update_vectors` |
| `build_workspace.py:51` | `BuildWorkspace.finish` | hashlib.sha256 | `"output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),` | |
| `covered_source_delivery.py:39` | `_validate_archive` | hashlib.sha256 | `or archive_record.get("sha256") != hashlib.sha256(blob).hexdigest()` | |
| `covered_source_delivery.py:70` | `_validate_archive` | hashlib.sha256 | `if len(value) != row.get("size_bytes") or hashlib.sha256(value).hexdigest() != row.get(` | |
| `covered_source_delivery.py:96` | `read_covered_source_companions` | hashlib.sha256 | `if hashlib.sha256(value).hexdigest() != digest:` | |
| `interfaces/commands/builder.py:79` | `handle_create_builder_chat` | hashlib.sha256 | `"session-builder-" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]` | |
| `interfaces/commands/builder.py:145` | `handle_ingest_builder_sources` | hashlib.sha256 | `+ hashlib.sha256(ingestion_id.encode("utf-8")).hexdigest()[:24]` | |
| `interfaces/commands/campaigns.py:349` | `handle_start_guided_campaign` | hashlib.sha256 | `"guided-ready-" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]` | |
| `interfaces/commands/campaigns.py:365` | `handle_start_guided_campaign` | hashlib.sha256 | `f"{hashlib.sha256(operation_id.encode('utf-8')).hexdigest()[:24]}"` | |
| `interfaces/commands/campaigns.py:406` | `handle_create_guided_campaign_draft` | hashlib.sha256 | `"session-guided-" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]` | |
| `interfaces/commands/campaigns.py:560` | `handle_start_guided_creation_subflow` | hashlib.sha256 | `"session-subflow-" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]` | |
| `interfaces/commands/campaigns.py:774` | `handle_confirm_new_campaign` | hashlib.sha256 | `"session-builder-" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]` | |
| `interfaces/commands/campaigns.py:837` | `handle_begin_campaign_construction` | hashlib.sha256 | `+ hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]` | |
| `interfaces/commands/campaigns.py:900` | `handle_finalize_campaign_construction` | hashlib.sha256 | `suffix = hashlib.sha256(campaign_id.encode("utf-8")).hexdigest()[:24]` | |
| `interfaces/commands/campaigns.py:911` | `handle_finalize_campaign_construction` | hashlib.sha256 | `+ hashlib.sha256(campaign_id.encode("utf-8")).hexdigest()[:24]` | |
| `interfaces/commands/sessions.py:669` | `handle_submit_session_input.publish_profile` | hashlib.sha256 | `operation_id=f"{session_id}:{hashlib.sha256(canonical).hexdigest()}",` | |
| `interfaces/commands/sessions.py:679` | `handle_submit_session_input.import_profile` | hashlib.sha256 | `f'{hashlib.sha256(data).hexdigest()}'` | |
| `interfaces/commands/sessions.py:748` | `handle_submit_session_input.publish_participant` | hashlib.sha256 | `operation_suffix = hashlib.sha256(content_basis).hexdigest()[:24]` | |
| `interfaces/commands/sessions.py:833` | `handle_submit_session_input.reuse_participant` | hashlib.sha256 | `"builder-character-copy-" + hashlib.sha256(copy_basis).hexdigest()[:32]` | |
| `interfaces/commands/sessions.py:908` | `handle_submit_session_input.publish_plan` | hashlib.sha256 | `suffix = hashlib.sha256( f"{session_id}:{materialized.closure_sha256}".encode() ).hexdigest()[:32]` | |
| `interfaces/commands/sessions.py:948` | `handle_submit_session_input.publish_setting` | hashlib.sha256 | `suffix = hashlib.sha256( f"{session_id}:{materialized.closure_sha256}".encode() ).hexdigest()[:32]` | |
| `interfaces/commands/sessions.py:985` | `handle_submit_session_input.run_import_repair` | hashlib.sha256 | `suffix = hashlib.sha256( f"{session_id}:{materialized.closure_sha256}".encode() ).hexdigest()[:32]` | |
| `interfaces/commands/sessions.py:1387` | `handle_start_dm_session` | hashlib.sha256 | `suffix = hashlib.sha256(campaign_id.encode("utf-8")).hexdigest()[:24]` | |
| `interfaces/owner_process.py:798` | `_admitted_dm_campaign_plan` | hashlib.sha256 | `suffix = hashlib.sha256(campaign_id.encode("utf-8")).hexdigest()[:24]` | |
| `interfaces/owner_process.py:1070` | `_install_plan_snapshot` | hashlib.sha256 | `install_id = f"plan-snapshot-{hashlib.sha256(snapshot).hexdigest()[:32]}"` | |
| `interfaces/owner_process.py:1146` | `_preinstall_bundled_srd` | hashlib.sha256 | `install_seed = hashlib.sha256(f"{RIFT_RULESET_ID}\0{version}".encode()).hexdigest()` | |
| `interfaces/owner_process.py:1167` | `_preinstall_bundled_riichi` | hashlib.sha256 | `install_id=f"install-bundled-riichi-{hashlib.sha256(archive).hexdigest()[:24]}",` | |
| `interfaces/owner_process.py:1460` | `_preflight_guided_start` | hashlib.sha256 | `"session-preflight-" + hashlib.sha256(campaign_id.encode("utf-8")).hexdigest()[:24]` | |
| `interfaces/webui.py:107` | `RemoteAccess.from_file` | hashlib.sha256 | `return cls(origin, hashlib.sha256(matched[1]).digest())` | `webui-remote` (the access-key file's digest, then a key sign-in) |
| `interfaces/webui.py:442` | `LocalWebUI.login` | hashlib.sha256 | `hashlib.sha256(key.encode()).digest(), self.remote.key_digest` | `webui-remote` (right and wrong access keys) |
| `interfaces/webui.py:525` | `LocalWebUI.command` | hashlib.sha256 | `digest = hashlib.sha256( json.dumps( [method, params], sort_keys=True, allow_nan=False, ).encode() ).hexdigest()` | `webui-guard` (a repeated request identity with other parameters: `idempotency_conflict`) |
| `local_dictation.py:42` | `_verified` | hashlib.file_digest | `return hashlib.file_digest(stream, "sha256").hexdigest() == expected[1]` | `dictation_vectors` (capabilities and prepare with complete, missing and corrupt model files) |
| `local_dictation.py:210` | `_device_id` | hashlib.sha256 | `identity = hashlib.sha256( f"{host}\n{row['name']}\n{row['default_samplerate']}".encode() ).hexdigest()[:16]` | `dictation_vectors` (`device_ids`: int and float rates, non-ASCII names; every capabilities scenario) |
| `portable_artifact.py:80` | `_digest` | hashlib.sha256 | `return hashlib.sha256(raw).hexdigest()` | |
| `release_artifact.py:124` | `_sha256_file` | hashlib.sha256 | `digest = hashlib.sha256()` | |
| `release_artifact.py:405` | `read_dependency_notices` | hashlib.sha256 | `if expected_sha256 is not None and hashlib.sha256(content).hexdigest() != expected_sha256:` | |
| `release_artifact.py:449` | `_manifest_dependency_notices` | hashlib.sha256 | `{"path": name, "sha256": hashlib.sha256(blob).hexdigest(), "size_bytes": len(blob)}` | |
| `release_artifact.py:582` | `assemble_release_layout` | hashlib.sha256 | `{"path": name, "sha256": hashlib.sha256(blob).hexdigest(), "size_bytes": len(blob)}` | |
| `release_artifact.py:588` | `assemble_release_layout` | hashlib.sha256 | `"sha256": hashlib.sha256(bundled_notices).hexdigest(),` | |
