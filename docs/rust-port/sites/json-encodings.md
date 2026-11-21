# JSON encoding call sites

Python snapshot `0da1010feb5a520f917f5d69ff8553ddb174e9e7`. 321 sites. Every `json.dumps` call and the `sylvia_base::pyjson::Dump` options that reproduce it. `+ default=` means non-JSON values are converted on the way out; port the conversion too.

Written by `docs/rust-port/tools/site_lists.py`. A worker ticks the Done column when the Rust side reproduces the site and a vector or comparison session proves it.

| Lane | Sites |
| --- | ---: |
| 1. Formats and content | 40 |
| 2. Game engine | 12 |
| 3. Storage and authority | 30 |
| 4. Providers and agents | 208 |
| 5. Owner, clients and release | 31 |

## Lane 1: Formats and content

| Where | Function | Kind | Code | Done |
| --- | --- | --- | --- | --- |
| `application/builder_references.py:404` | `reader_for_references` | Dump::INDENT2.ascii(false) | `plan_files[example["path"]] = json.dumps( json.loads(canonical_json_bytes(example["draft"])), indent=2, ensure_ascii=False, ) + "\n"` | |
| `application/builder_references.py:410` | `reader_for_references` | Dump::INDENT2.ascii(false) | `plan_files["character-profile/profile.json"] = json.dumps( dict(profile.profile), ensure_ascii=False, indent=2, )` | |
| `application/builder_references.py:415` | `reader_for_references` | Dump::INDENT2.ascii(false) | `"campaign-plan/plan.json": json.dumps( json.loads(canonical_json_bytes(plan["payload"])), indent=2, ensure_ascii=False ),` | |
| `application/bundled_packages.py:488` | `read_bundled_rift_source` | Dump::CANONICAL | `return json.dumps( { "binding": authority.ruleset.as_payload(), "campaign_setting": authority.setting.as_payload(), "package_closure_sha256": autho...` | |
| `builder/_interview_extractor.py:197` | `InterviewExtractor._build_extraction_prompt` | Dump::INDENT2 | `lines.append(json.dumps(raw_answers, indent=2))` | |
| `campaign/_folder.py:466` | `_write_status_atomic` | Dump::INDENT2 | `tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")` | |
| `campaign/_folder.py:502` | `_update_status` | Dump::INDENT2 | `tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")` | |
| `domain/model_capability.py:327` | `_canonical_bytes` | Dump::CANONICAL + allow_nan= | `return json.dumps( value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False, ).encode("utf-8")` | |
| `domain/package_library.py:573` | `canonical_json_bytes` | Dump::CANONICAL + allow_nan= | `return json.dumps( thaw_json_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False, ).encode("utf-8")` | `sylvia-domain` identity_vectors |
| `domain/package_library.py:1480` | `validate_content_declaration_links` | Dump::DEFAULT | `detail = json.dumps(sorted(linked - local)[:8])` | |
| `domain/package_library.py:1495` | `validate_content_declaration_links` | Dump::DEFAULT | `detail = json.dumps(sorted(local - linked)[:8])` | |
| `domain/session.py:123` | `builder_dm_handoff_payload_sha256` | Dump::CANONICAL + allow_nan= | `encoded = json.dumps( { "handoff_id": handoff_id, "campaign_id": campaign_id, "builder_session_id": builder_session_id, "dm_session_id": dm_session...` | |
| `domain/session.py:222` | `campaign_plan_payload_sha256` | Dump::CANONICAL + allow_nan= | `encoded = json.dumps( payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False, ).encode("utf-8")` | |
| `domain/session.py:290` | `agent_session_payload_sha256` | Dump::CANONICAL + allow_nan= | `encoded = json.dumps( { "session_id": record.session_id, "campaign_id": record.campaign_id, "role": record.role.value, "provider_id": record.provid...` | |
| `domain/session.py:488` | `builder_chat_payload_sha256` | Dump::CANONICAL + allow_nan= | `encoded = json.dumps( { "session": agent_session_payload_sha256(record.session), "display_name": record.display_name, "created_ordinal": record.cre...` | |
| `state/_changelog.py:111` | `Changelog.append` | Dump::DEFAULT.ascii(false) | `f.write(json.dumps(entry, ensure_ascii=False) + "\n")` | |
| `state/_changelog.py:146` | `Changelog.append_batch` | Dump::DEFAULT.ascii(false) | `f.write(json.dumps(full_entry, ensure_ascii=False) + "\n")` | |
| `state/_store.py:179` | `StateStore.create` | Dump::INDENT2.ascii(false) | `json.dump(state, f, indent=2, ensure_ascii=False)` | |
| `state/_store.py:259` | `StateStore.save` | Dump::INDENT2.ascii(false) | `json.dump(self._data, f, indent=2, ensure_ascii=False)` | |
| `state/state_api.py:226` | `StateAPI.export_snapshot` | Dump::INDENT2.ascii(false) | `json.dump(snap, f, indent=2, ensure_ascii=False)` | |

## Lane 2: Game engine

| Where | Function | Kind | Code | Done |
| --- | --- | --- | --- | --- |
| `application/_runtime_cache.py:37` | `cached_runtime` | Dump::DEFAULT.sorted(true) | `key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()` | |
| `application/_runtime_cache.py:44` | `cached_runtime` | Dump::WIRE.sorted(true) + allow_nan= | `encoded = json.dumps( raw, sort_keys=True, separators=(",", ":"), allow_nan=False ).encode()` | |
| `application/_runtime_cache.py:60` | `cached_runtime` | Dump::WIRE.sorted(true) + allow_nan= | `encoded = json.dumps(raw, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()` | |
| `application/_runtime_cache.py:61` | `cached_runtime` | Dump::WIRE + allow_nan= | `document = json.dumps({ "identity": identity, "payload_sha256": hashlib.sha256(encoded).hexdigest(), "ruleset": raw, }, separators=(",", ":"), allo...` | |
| `application/actor_decisions.py:168` | `project_actor_decision` | Dump::CANONICAL | `body = json.dumps( {**request.observation_payload(), "instructions": request.instructions}, sort_keys=True, ensure_ascii=False, separators=(",", ":...` | |
| `application/actor_decisions.py:178` | `project_actor_decision` | Dump::WIRE | `identity = json.dumps( [ request.campaign_id, request.turn_id, request.package_id, request.package_version, request.package_sha256, request.actor_i...` | |

## Lane 3: Storage and authority

| Where | Function | Kind | Code | Done |
| --- | --- | --- | --- | --- |
| `application/authority.py:9152` | `AuthorityOwner.export_admin_full_audit` | Dump::INDENT2.ascii(false) + allow_nan= | `encoded = json.dumps( thaw_json_value(trace), ensure_ascii=False, allow_nan=False, indent=2, ).encode("utf-8")` | |
| `application/authority.py:9164` | `AuthorityOwner.export_admin_full_audit` | Dump::INDENT2.ascii(false) + allow_nan= | `encoded = json.dumps( thaw_json_value(diagnostic), ensure_ascii=False, allow_nan=False, indent=2, ).encode("utf-8")` | |
| `application/authority.py:9915` | `_semantic_hash` | Dump::CANONICAL + allow_nan= | `encoded = json.dumps( payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False, )` | |
| `infrastructure/builder_source_ingestion.py:425` | `stage_builder_sources_for_purge` | Dump::WIRE.sorted(true) | `marker_bytes = json.dumps( _purge_marker(campaign_id, operation_id), sort_keys=True, separators=(",", ":"), ).encode("utf-8")` | |
| `infrastructure/campaign_archive.py:688` | `_json_bytes` | Dump::WIRE.ascii(false) + allow_nan= | `return json.dumps( value, sort_keys=False, separators=(",", ":"), ensure_ascii=False, allow_nan=False, ).encode("utf-8")` | |
| `infrastructure/private_records_archive.py:79` | `encode` | Dump::WIRE.ascii(false) + allow_nan= | `line = (json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")` | |
| `infrastructure/sqlite_authority.py:869` | `_dump` | Dump::CANONICAL + allow_nan= | `return json.dumps( thaw_json_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False, )` | |
| `infrastructure/sqlite_authority.py:2220` | `SQLiteAuthorityStore._read_export_rows` | Dump::CANONICAL + allow_nan= | `materialized["content_json"] = json.dumps( content, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False, )` | |
| `infrastructure/sqlite_authority.py:6318` | `SQLiteAuthorityStore.campaign_session_contexts` | Dump::DEFAULT | `(row["session_id"], json.dumps(evidence_kinds)),` | |
| `infrastructure/sqlite_authority.py:10900` | `SQLiteAuthorityStore.response_window_actions` | Dump::DEFAULT | `"attempts": json.dumps(sorted(response_attempts or ()))},` | |
| `infrastructure/sqlite_authority.py:10925` | `SQLiteAuthorityStore.player_action_markers` | Dump::DEFAULT | `(marker_kind, campaign_id, json.dumps(action_ids)),` | |
| `infrastructure/sqlite_authority.py:12307` | `SQLiteAuthorityStore._read_snapshot` | Dump::DEFAULT | `json.dumps(display_action_ids),` | |
| `infrastructure/sqlite_continuity.py:49` | `_dump` | Dump::CANONICAL | `return json.dumps( thaw_json_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, )` | |
| `infrastructure/sqlite_recovery.py:258` | `result_json` | Dump::WIRE.ascii(false) + allow_nan= | `return json.dumps( { "campaign_id": result.campaign_id, "captured_revision": result.captured_revision, "archive_sha256": result.archive_sha256, "by...` | |
| `infrastructure/sqlite_session.py:306` | `_dump` | Dump::CANONICAL + allow_nan= | `return json.dumps( thaw_json_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False, )` | |

## Lane 4: Providers and agents

| Where | Function | Kind | Code | Done |
| --- | --- | --- | --- | --- |
| `agent/_actor_decisions.py:29` | `_json` | Dump::CANONICAL | `return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))` | |
| `agent/_builder_session.py:234` | `_provider_history` | Dump::DEFAULT.ascii(false) | `"content": json.dumps(_json_value(content["result"]), ensure_ascii=False),` | |
| `agent/_builder_session.py:285` | `_provider_history` | Dump::CANONICAL | `application_data = json.dumps( {"kind": message.kind.value, "content": _json_value(content)}, sort_keys=True, separators=(",", ":"), ensure_ascii=F...` | |
| `agent/_builder_session.py:854` | `_campaign_content_tools.discover` | Dump::DEFAULT.ascii(false).sorted(true) | `searchable = json.dumps(item, sort_keys=True, ensure_ascii=False).casefold()` | |
| `agent/_builder_session.py:1552` | `_campaign_plan_example` | Dump::CANONICAL | `return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` | |
| `agent/_builder_session.py:1604` | `_campaign_source_tool.inspect` | Dump::CANONICAL | `content = json.dumps( { "binding": authority.ruleset.as_payload(), "campaign_setting": authority.setting.as_payload(), "package_closure_sha256": au...` | |
| `agent/_character_draft_tool.py:62` | `character_draft_tool.open_draft` | Dump::INDENT2.ascii(false) | `content = json.dumps(dict(draft), ensure_ascii=False, indent=2) + "\n"` | |
| `agent/_character_profile_tools.py:142` | `character_profile_tools.execute` | Dump::DEFAULT.ascii(false) | `if len(json.dumps(value, ensure_ascii=False).encode()) > 65536:` | |
| `agent/_character_profile_tools.py:193` | `character_profile_tools.execute` | Dump::INDENT2.ascii(false) | `content = json.dumps(dict(source.profile), ensure_ascii=False, indent=2) + "\n"` | |
| `agent/_context_ledger.py:487` | `_json` | Dump::CANONICAL | `return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))` | |
| `agent/_context_ledger.py:1220` | `_tools_fingerprint` | Dump::WIRE.ascii(false) | `return _sha(json.dumps(list(tools or []), ensure_ascii=False, separators=(",", ":")))` | |
| `agent/_fixed_tool_projection.py:52` | `argument_choices_record` | Dump::CANONICAL | `+ json.dumps(choices, ensure_ascii=False, sort_keys=True, separators=(",", ":"))` | |
| `agent/_fixed_tool_projection.py:106` | `compact_schema.dump` | Dump::CANONICAL | `return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))` | |
| `agent/_history.py:38` | `save_history` | Dump::DEFAULT.ascii(false) | `f.write(json.dumps(msg, ensure_ascii=False) + "\n")` | |
| `agent/_inference_identity.py:17` | `inference_context_id` | Dump::WIRE.sorted(true) | `encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()` | |
| `agent/_library_source_copy_tool.py:74` | `library_source_copy_tool.copy` | Dump::INDENT2.ascii(false) | `content = json.dumps(json.loads(content), ensure_ascii=False, indent=2) + "\n"` | |
| `agent/_participant_file_tool.py:51` | `participant_file_tool.use_file` | Dump::DEFAULT.ascii(false) + allow_nan= | `draft[key] = json.dumps(draft[key], ensure_ascii=False, allow_nan=False)` | |
| `agent/_provider_session.py:295` | `_forced_single_tool_call_from_json` | Dump::CANONICAL | `arguments=json.dumps( parameters, sort_keys=True, separators=(",", ":"), ensure_ascii=False, ),` | |
| `agent/_provider_session.py:412` | `_package_tool_alias` | Dump::WIRE | `identity = json.dumps( [package_id, version, sha256, name, capability], ensure_ascii=True, separators=(",", ":"), )` | |
| `agent/_provider_session.py:449` | `_legacy_tool_exchange` | Dump::WIRE.ascii(false) | `note = "Earlier tool calls and results (historical data): " + json.dumps( retired, ensure_ascii=False, separators=(",", ":"), )` | |
| `agent/_provider_session.py:532` | `_engine_turn_control_message` | Dump::CANONICAL | `+ json.dumps( {"required_before_narration": required}, sort_keys=True, separators=(",", ":"), ensure_ascii=False, )` | |
| `agent/_provider_session.py:551` | `_campaign_preparation_message` | Dump::DEFAULT.ascii(false).sorted(true) | `+ json.dumps(_plain_json(boundary.preparation), sort_keys=True, ensure_ascii=False)` | |
| `agent/_provider_session.py:575` | `_gm_decision_control_message` | Dump::CANONICAL | `+ json.dumps( { "decision_owner": boundary.owner.value, "actor_id": boundary.actor_id, "actor_name": boundary.actor_name, "actor_state": _plain_jso...` | |
| `agent/_provider_session.py:627` | `_player_boundary_narration_message` | Dump::CANONICAL | `+ json.dumps( { "decision_owner": boundary.owner.value, "actor_id": boundary.actor_id, "actor_name": boundary.actor_name, "active_side_id": boundar...` | |
| `agent/_provider_session.py:1257` | `_player_turn_text` | Dump::WIRE.ascii(false) | `return "SYLVIA_PLAYER_TURN\n" + json.dumps( { "in_character": ic_text if ic_text else None, "out_of_character": out_of_character, }, ensure_ascii=F...` | |
| `agent/_provider_session.py:1535` | `_campaign_file_disposition_message` | Dump::CANONICAL | `+ json.dumps(task, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` | |
| `agent/_provider_session.py:1561` | `_campaign_file_updated_narration_message` | Dump::CANONICAL | `"last:\n" + json.dumps(task, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` | |
| `agent/_provider_session.py:1586` | `_campaign_opening_task_message` | Dump::CANONICAL | `+ json.dumps( _plain_json(opening_footing), sort_keys=True, separators=(",", ":"), ensure_ascii=False, )` | |
| `agent/_provider_session.py:1921` | `ProviderSessionAdapter.for_retry.narrate` | Dump::CANONICAL | `+ json.dumps( safe_basis, sort_keys=True, separators=(",", ":"), ensure_ascii=False, )` | |
| `agent/_provider_session.py:2428` | `ProviderSessionAdapter._relevant_character_files_message` | Dump::CANONICAL | `+ json.dumps( { "interpretation": ( "These are the exact Character Files selected by an unambiguous " "roster-name match in the current player word...` | |
| `agent/_provider_session.py:2687` | `ProviderSessionAdapter._participant_result_message` | Dump::DEFAULT.ascii(false).sorted(true) | `+ json.dumps(_plain_json(result), ensure_ascii=False, sort_keys=True)` | |
| `agent/_provider_session.py:2721` | `ProviderSessionAdapter._request_messages` | Dump::DEFAULT.ascii(false) | `+ json.dumps(current.get("content"), ensure_ascii=False)` | |
| `agent/_provider_session.py:2879` | `ProviderSessionAdapter._messages` | Dump::DEFAULT.ascii(false) | `+ json.dumps(_plain_json(player_actions), ensure_ascii=False)` | |
| `agent/_provider_session.py:3028` | `ProviderSessionAdapter._messages` | Dump::CANONICAL | `+ json.dumps( {"text": narration}, sort_keys=True, separators=(",", ":"), ensure_ascii=False, )` | |
| `agent/_provider_session.py:3345` | `ProviderSessionAdapter._messages` | Dump::DEFAULT.ascii(false) | `+ json.dumps({ "action_id": response_message.content.get("player_action_id"), "automatic": response_message.content.get("automatic_response", False...` | |
| `agent/_provider_session.py:3362` | `ProviderSessionAdapter._messages` | Dump::CANONICAL | `+ json.dumps( { "event": "campaign_start", "opening_footing": self._opening_footing_payload( provider_input ), }, sort_keys=True, separators=(",", ...` | |
| `agent/_provider_session.py:3459` | `ProviderSessionAdapter._messages` | Dump::DEFAULT.ascii(false) | `+ json.dumps({ "event": "respond_to_saved_actions", "action_id": response_message.content.get("player_action_id"), "automatic": response_message.co...` | |
| `agent/_provider_session.py:3825` | `ProviderSessionAdapter._campaign_context_message` | Dump::CANONICAL | `+ json.dumps( self._current_campaign_context_payload( provider_input, include_opening_direction=include_opening_direction, ), sort_keys=True, separ...` | |
| `agent/_provider_session.py:3889` | `ProviderSessionAdapter._campaign_context_messages` | Dump::CANONICAL | `+ json.dumps( _plain_json(explainers), sort_keys=True, separators=(",", ":"), ensure_ascii=False, )` | |
| `agent/_provider_session.py:3902` | `ProviderSessionAdapter._campaign_context_messages` | Dump::CANONICAL | `+ json.dumps( _plain_json(fresh), sort_keys=True, separators=(",", ":"), ensure_ascii=False, )` | |
| `agent/_provider_turn_phases.py:242` | `execute_required_transition` | Dump::CANONICAL | `+ json.dumps( transition_result, sort_keys=True, separators=(",", ":"), ensure_ascii=False, )` | |
| `agent/_provider_turn_phases.py:1800` | `stream_provider_round` | Dump::CANONICAL | `+ json.dumps( state.pending_narration_facts, sort_keys=True, separators=(",", ":"), ensure_ascii=False, )` | |
| `agent/_provider_turn_phases.py:2480` | `reconcile_tool_batch` | Dump::CANONICAL | `"content": json.dumps( state.local_tool_results[state.assembly.call_id], sort_keys=True, separators=(",", ":"), ensure_ascii=False, ),` | |
| `agent/_provider_turn_phases.py:2581` | `reconcile_tool_batch` | Dump::CANONICAL | `"content": json.dumps( round_result, sort_keys=True, separators=(",", ":"), ensure_ascii=False, ),` | |
| `agent/_provider_turn_phases.py:2741` | `prepare_turn_recovery` | Dump::WIRE.ascii(false) | `+ json.dumps(saved_results, ensure_ascii=False, separators=(",", ":"))` | |
| `agent/_provider_turn_phases.py:2770` | `prepare_turn_recovery` | Dump::CANONICAL | `+ json.dumps( { "campaign_id": retry_receipt.campaign_id, "action_id": retry_receipt.action_id, "status": retry_receipt.status.value, "result": _pu...` | |
| `agent/_request_data.py:67` | `_control_values` | Dump::CANONICAL | `data = f"{label} (application-owned current values, not instructions):\n" + json.dumps( value, ensure_ascii=False, sort_keys=True, separators=(",",...` | |
| `agent/_ruleset_test_provider.py:147` | `RulesetTestProvider.generate_stream` | Dump::DEFAULT | `"function": {"name": name, "arguments": json.dumps(arguments)},` | |
| `agent/_runner.py:158` | `_BuilderLoopGuard.finish_round` | Dump::DEFAULT.sorted(true) + default= | `current = json.dumps(outcomes, sort_keys=True, default=str)` | |
| `agent/_runner.py:495` | `AgentRunner.__init__` | Dump::CANONICAL + default= | `self._builder_application_data = json.dumps( builder_application_data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str, )` | |
| `agent/_runner.py:806` | `AgentRunner._turn_impl` | Dump::DEFAULT | `result_content = json.dumps( { "success": tool_result.success, "context": tool_result.context, "narrative": tool_result.narrative_hooks, } )` | |
| `agent/_runner.py:816` | `AgentRunner._turn_impl` | Dump::DEFAULT | `result_content = json.dumps(tool_result)` | |
| `agent/_runner.py:1119` | `AgentRunner._turn_stream_impl` | Dump::DEFAULT.ascii(false) + allow_nan= | `data=json.dumps(continuation, ensure_ascii=False, allow_nan=False),` | |
| `agent/_runner.py:1206` | `AgentRunner._turn_stream_impl` | Dump::DEFAULT | `result_content = json.dumps( { "success": tool_result.success, "context": tool_result.context, "narrative": tool_result.narrative_hooks, } )` | |
| `agent/_runner.py:1215` | `AgentRunner._turn_stream_impl` | Dump::DEFAULT | `result_content = json.dumps(tool_result)` | |
| `agent/_runner.py:1301` | `AgentRunner._turn_stream_impl` | Dump::DEFAULT | `"content": json.dumps( {"success": False, "code": "not_executed", "reason": error.reason} ),` | |
| `agent/_runner.py:2138` | `AgentRunner._replace_single_widget` | Dump::DEFAULT | `real_content = json.dumps( { "success": True, "status": "answered", "widget_id": widget_id, "answer": answer, } )` | |
| `agent/_session_loop.py:264` | `ProviderEvent.private_continuation` | Dump::DEFAULT.ascii(false) + allow_nan= | `text=json.dumps(payload, ensure_ascii=False, allow_nan=False),` | |
| `agent/_stateless_mcp.py:1372` | `_safe_error` | Dump::WIRE.sorted(true) | `types.TextContent(text=json.dumps(payload, sort_keys=True, separators=(",", ":")))` | |
| `agent/_stateless_mcp.py:1721` | `build_stateless_mcp_server.call_tool` | Dump::CANONICAL | `text=json.dumps( plain_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, )` | |
| `agent/_stateless_mcp.py:1742` | `build_stateless_mcp_server.call_tool` | Dump::CANONICAL | `text=json.dumps( plain_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, )` | |
| `agent/_stateless_mcp.py:1922` | `build_stateless_mcp_server.call_tool` | Dump::WIRE.sorted(true) | `text=json.dumps(payload, sort_keys=True, separators=(",", ":"))` | |
| `agent/_stateless_mcp.py:1968` | `build_stateless_mcp_server.call_tool` | Dump::CANONICAL | `text=json.dumps( _plain_json(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False, )` | |
| `agent/_stateless_mcp.py:2001` | `build_stateless_mcp_server.call_tool` | Dump::CANONICAL | `text=json.dumps( _plain_json(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False, )` | |
| `agent/_stateless_mcp.py:2095` | `build_stateless_mcp_server.call_tool` | Dump::WIRE.sorted(true) | `text=json.dumps(action_payload, sort_keys=True, separators=(",", ":"))` | |
| `agent/_stateless_mcp.py:2347` | `StatelessMcpMechanics.commit_campaign_file` | Dump::CANONICAL | `identity_payload = json.dumps( { "campaign_id": campaign_id, "session_id": session_id, "basis_message_id": basis_message_id, "turn_id": resolved_tu...` | |
| `agent/_stateless_mcp.py:2485` | `StatelessMcpMechanics.commit_narrative_state` | Dump::CANONICAL | `identity_payload = json.dumps( { "campaign_id": campaign_id, "session_id": session_id, "basis_message_id": basis_message_id, "turn_id": resolved_tu...` | |
| `agent/_tool_loader.py:96` | `_literal` | Dump::DEFAULT.ascii(false) | `return json.dumps(value, ensure_ascii=False)` | |
| `agent/_tool_loader.py:373` | `_compact_json` | Dump::WIRE.ascii(false) | `return json.dumps(value, ensure_ascii=False, separators=(",", ":"))` | |
| `agent/_working_context.py:69` | `_json` | Dump::CANONICAL | `return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))` | |
| `application/character_chat.py:176` | `_encode` | indent=1.ascii(false) | `return json.dumps( { "format_id": FORMAT_ID, "format_version": FORMAT_VERSION, "chat_id": chat.chat_id, "character": { "artifact_id": chat.artifact...` | |
| `config/_credential_protection.py:251` | `protect_credentials` | Dump::DEFAULT.ascii(false) | `payload = json.dumps( {"api_key": api_key, "headers": dict(headers)}, ensure_ascii=False ).encode()` | |
| `config/claude_settings.py:289` | `main` | Dump::INDENT2 | `print(json.dumps(result, indent=2))` | |
| `config/loader.py:234` | `load_config` | Dump::DEFAULT | `provider["credential_overrides"] = SecretStr(json.dumps(overrides))` | |
| `config/loader.py:242` | `load_config` | Dump::DEFAULT | `provider["protected_credentials"] = SecretStr(json.dumps(envelope))` | |
| `provider/_admin_audit.py:83` | `_serialization_projection` | Dump::CANONICAL + allow_nan= | `encoded = json.dumps( _plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False, ).encode("utf-8")` | |
| `provider/_anthropic.py:388` | `AnthropicMessagesTransport.parse_response` | Dump::DEFAULT | `arguments=json.dumps( block.get("input", {}) if isinstance(block, dict) else getattr(block, "input", {}) ),` | |
| `provider/_anthropic.py:491` | `AnthropicMessagesTransport.parse_stream_chunk` | Dump::WIRE.sorted(true) | `"arguments": json.dumps( initial_input, sort_keys=True, separators=(",", ":"), )` | |
| `provider/_chatgpt.py:319` | `_in_place_answer` | Dump::DEFAULT.ascii(false) | `result = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)` | |
| `provider/_chatgpt.py:1313` | `ChatGPTProvider._attempt` | Dump::DEFAULT + allow_nan= | `arguments = json.dumps( params.get("arguments"), ensure_ascii=True, allow_nan=False )` | |
| `provider/_claude.py:131` | `prepare_request` | Dump::DEFAULT + allow_nan= | `history = json.loads(json.dumps(request["messages"], allow_nan=False))` | |
| `provider/_claude.py:203` | `_replay_record` | Dump::DEFAULT | `add(str(saved["type"]), {"message": json.loads(json.dumps(saved["message"]))})` | |
| `provider/_claude.py:322` | `_native_projection` | Dump::DEFAULT | `add("user", {"message": json.loads(json.dumps(answer))})` | |
| `provider/_claude.py:608` | `ClaudeProvider._keep_plan` | Dump::DEFAULT.sorted(true) | `tools_key=json.dumps(native_tools, sort_keys=True), history=history,` | |
| `provider/_claude.py:796` | `ClaudeProvider._fresh` | Dump::DEFAULT | `transcript = "".join(json.dumps(r, ensure_ascii=True) + "\n" for r in records)` | |
| `provider/_claude.py:1033` | `ClaudeProvider._run_events` | Dump::DEFAULT + allow_nan= | `json.dumps(candidate["input"], allow_nan=False),` | |
| `provider/_claude_kept.py:265` | `_same` | Dump::DEFAULT.ascii(false).sorted(true) | `return json.dumps(_plain(a), sort_keys=True, ensure_ascii=False) == json.dumps(` | |
| `provider/_claude_kept.py:265` | `_same` | Dump::DEFAULT.ascii(false).sorted(true) | `return json.dumps(_plain(a), sort_keys=True, ensure_ascii=False) == json.dumps( _plain(b), sort_keys=True, ensure_ascii=False)` | |
| `provider/_claude_native.py:297` | `_round_row` | Dump::DEFAULT | `return {"type": "assistant", "message": json.loads(json.dumps(message))}` | |
| `provider/_claude_native.py:298` | `_round_row` | Dump::DEFAULT | `return {"type": "user", "message": json.loads(json.dumps( {key: message[key] for key in ("role", "content") if key in message}))}` | |
| `provider/_claude_native.py:480` | `collect_answer` | Dump::DEFAULT | `"collected", tuple(records), answer=json.loads(json.dumps(rows[index]["message"])),` | |
| `provider/_claude_runtime.py:401` | `ClaudeConnection.write` | Dump::DEFAULT + allow_nan= | `process.stdin.write(json.dumps(message, ensure_ascii=True, allow_nan=False) + "\n")` | |
| `provider/_codex_rpc.py:294` | `CodexConnection._write` | Dump::DEFAULT + allow_nan= | `process.stdin.write(json.dumps(message, ensure_ascii=True, allow_nan=False) + "\n")` | |
| `provider/_context_fit.py:85` | `_canonical_request_bytes` | Dump::CANONICAL + allow_nan= | `return json.dumps( {"messages": messages, "tools": tools or [], "options": dict(options)}, sort_keys=True, separators=(",", ":"), ensure_ascii=Fals...` | |
| `provider/_context_fit.py:97` | `_canonical_value_bytes` | Dump::CANONICAL + allow_nan= | `return json.dumps( value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False, ).encode("utf-8")` | |
| `provider/_gemini.py:228` | `GeminiProvider._parse` | Dump::DEFAULT | `arguments=json.dumps(fn.get("args", {})),` | |
| `provider/_hooks.py:443` | `DumpHook._atomic_write` | Dump::INDENT2 + default= | `tmp_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")` | |
| `provider/_token_estimate.py:13` | `_size` | Dump::CANONICAL | `return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))` | |
| `session/_prompt_assembler.py:451` | `PromptAssembler.build_character` | Dump::DEFAULT.sorted(true) + default= | `state_payload = json.dumps( {"characters": characters_dict, "scene_tags": sorted(tags)}, sort_keys=True, default=str, )` | |
| `session/_prompt_assembler.py:746` | `PromptAssembler.build_history_with_indices._msg_len` | Dump::DEFAULT + default= | `return len(json.dumps(content, default=str))` | |
| `session/_prompt_assembler.py:859` | `PromptAssembler.assemble` | Dump::DEFAULT.sorted(true) + default= | `context_hash_input = json.dumps( { "campaign": campaign, }, sort_keys=True, default=str, )` | |
| `session/_prompt_assembler.py:1060` | `PromptAssembler._build_context_cached` | Dump::DEFAULT.sorted(true) + default= | `context_hash_input = json.dumps( {"campaign": campaign}, sort_keys=True, default=str, )` | |
| `session/_token_budget.py:113` | `TokenEstimator.estimate_messages` | Dump::DEFAULT + default= | `total += self.estimate(json.dumps(content, default=str))` | |
| `tools/_dm_campaign_handlers.py:237` | `tool_advance_act` | Dump::INDENT2 | `tmp_path.write_text(json.dumps(status, indent=2), encoding="utf-8")` | |

## Lane 5: Owner, clients and release

| Where | Function | Kind | Code | Done |
| --- | --- | --- | --- | --- |
| `application/desktop_update/helper.py:260` | `_write` | Dump::WIRE | `stream.write(json.dumps(value, separators=(",", ":")) + "\n")` | |
| `application/desktop_update/identity.py:34` | `main` | Dump::WIRE | `print(json.dumps(runtime_identity(), separators=(",", ":")))` | |
| `application/desktop_update/manifest.py:140` | `canonical_bytes` | Dump::CANONICAL + allow_nan= | `return json.dumps( value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False ).encode("utf-8")` | `local-services`, `sylvia-owner` diagnostics tests |
| `application/desktop_update/recovery_service.py:138` | `_write` | Dump::WIRE | `stream.write(json.dumps(row, separators=(",", ":")) + "\n")` | |
| `build_workspace.py:29` | `BuildWorkspace.create` | Dump::DEFAULT | `(path / ".invocation.json").write_text(json.dumps({"token": token, "pid": os.getpid()}))` | |
| `build_workspace.py:65` | `BuildWorkspace.finish` | Dump::INDENT2 | `json.dump(record, stream, indent=2)` | |
| `interfaces/_local_diagnostics.py:137` | `_encode` | Dump::WIRE + allow_nan= | `(json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")` | `local-services`, `sylvia-owner` diagnostics tests |
| `interfaces/_local_diagnostics.py:373` | `LocalDiagnostics.preview` | Dump::INDENT2 + allow_nan= | `content = json.dumps(payload, ensure_ascii=True, indent=2, allow_nan=False) + "\n"` | `local-services`, `sylvia-owner` diagnostics tests |
| `interfaces/commands/providers.py:96` | `handle_admin_full_audit_record` | Dump::WIRE + allow_nan= | `encoded = json.dumps( _json_value(record), ensure_ascii=True, separators=(",", ":"), allow_nan=False, ).encode("utf-8")` | |
| `interfaces/owner_process.py:847` | `_write_message` | Dump::WIRE + allow_nan= | `encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), allow_nan=False)` | |
| `interfaces/owner_process.py:903` | `_json_value` | Dump::CANONICAL | `key=lambda item: json.dumps( item, ensure_ascii=False, separators=(",", ":"), sort_keys=True ),` | |
| `interfaces/ruleset_test_worker.py:91` | `main` | Dump::DEFAULT | `args.result.write_text(json.dumps(result, ensure_ascii=True), encoding="utf-8")` | |
| `interfaces/webui.py:164` | `OwnerProcess._write` | Dump::DEFAULT + allow_nan= | `raw = json.dumps(payload, ensure_ascii=True, allow_nan=False).encode() + b"\n"` | |
| `interfaces/webui.py:526` | `LocalWebUI.command` | Dump::DEFAULT.sorted(true) + allow_nan= | `json.dumps( [method, params], sort_keys=True, allow_nan=False, ).encode()` | |
| `interfaces/webui.py:564` | `LocalWebUI.trim_receipts` | Dump::DEFAULT | `self.receipt_sizes[identity] = 1024 if error else len(json.dumps(task.result()).encode())` | |
| `local_dictation_process.py:57` | `serve.reply` | Dump::WIRE | `output.write(json.dumps(value, ensure_ascii=True, separators=(",", ":")) + "\n")` | |
| `portable_artifact.py:76` | `_json` | Dump::WIRE.sorted(true) | `return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()` | |
| `release_artifact.py:117` | `_canonical_json_bytes` | Dump::CANONICAL | `json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"` | |
