# Regular expressions

Python snapshot `0da1010feb5a520f917f5d69ff8553ddb174e9e7`. 412 sites. Every regular expression. Rows marked NEEDS fancy-regex use constructs the `regex` crate rejects. Python's `re` and Rust engines also differ on `\d`, `\w`, `\s` and `\b` (Unicode-aware in both by default, but not the same tables) and on `$` (Python's also matches before a final newline). Test every pattern against Python's verdicts.

Written by `docs/rust-port/tools/site_lists.py`. A worker ticks the Done column when the Rust side reproduces the site and a vector or comparison session proves it.

| Lane | Sites |
| --- | ---: |
| 1. Formats and content | 146 |
| 2. Game engine | 90 |
| 3. Storage and authority | 48 |
| 4. Providers and agents | 82 |
| 5. Owner, clients and release | 46 |

## Lane 1: Formats and content

| Where | Function | Kind | Code | Done |
| --- | --- | --- | --- | --- |
| `application/bundled_campaign_plans.py:120` | `read_plan_catalogue` | re.fullmatch; plain | `if not isinstance(row, dict) or not isinstance(row.get("id"), str) or re.fullmatch( r"[a-z0-9]+(?:-[a-z0-9]+)*", row["id"], ) is None or row["id"] ...` | |
| `application/character_card_import.py:435` | `_original_path` | re.fullmatch; plain | `if not isinstance(digest, str) or re.fullmatch("[0-9a-f]{64}", digest) is None:` | |
| `application/character_card_import.py:558` | `_retire_completed_previews` | re.fullmatch; plain | `if re.fullmatch("[0-9a-f]{64}", target.name) is None:` | |
| `application/character_card_import.py:611` | `confirm_card_preview` | re.fullmatch; plain | `if not isinstance(digest, str) or re.fullmatch("[0-9a-f]{64}", digest) is None:` | |
| `application/character_profiles.py:29` | `<module>` | re.compile; plain | `PROFILE_ID = re.compile(r"character-profile-[0-9a-f]{24}-[0-9a-f]{16}")` | |
| `application/character_profiles.py:30` | `<module>` | re.compile; plain | `_SHA256 = re.compile(r"[0-9a-f]{64}")` | |
| `application/character_rules.py:18` | `<module>` | re.compile; plain | `_LOCAL = re.compile(r"_[A-Za-z0-9_]+\Z")` | |
| `application/compatible_character_copy.py:23` | `<module>` | re.compile; plain | `_SHA256 = re.compile(r"[0-9a-f]{64}")` | |
| `application/import_repair.py:55` | `<module>` | re.compile; plain | `_PLAN_ID_PATTERN = re.compile(r"campaign-plan-reusable-[0-9a-f]{24}-[0-9a-f]{16}")` | |
| `application/import_repair.py:274` | `stage_repair_copy` | re.fullmatch; plain | `if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:` | |
| `application/import_repair_sessions.py:91` | `start_import_repair_chat` | re.fullmatch; plain | `if re.fullmatch(r"[0-9a-f]{64}", repair_id) is None:` | |
| `application/initial_character_state.py:33` | `<module>` | re.compile; plain | `_FIELD_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")` | |
| `application/plan_bundle.py:119` | `_read_bundle` | re.fullmatch; plain | `or re.fullmatch(r"campaign-plan-reusable-[0-9a-f]{24}-[0-9a-f]{16}", identity) is None` | |
| `application/reusable_participants.py:48` | `<module>` | re.compile; plain | `_ARTIFACT_ID = re.compile(r"participant-[0-9a-f]{24}-[0-9a-f]{16}")` | |
| `application/reusable_participants.py:584` | `delete_reusable_participant` | re.fullmatch; plain | `if re.fullmatch(r"[0-9a-f]{64}", expected_content_sha256) is None:` | |
| `application/reusable_plans.py:94` | `_strip_section` | re.compile; pattern built at run time, check by hand | `pattern = re.compile( rf"\n+## {re.escape(heading)}\s*\n.*?(?=\n## \|\Z)", flags=re.DOTALL, )` | |
| `application/reusable_plans.py:325` | `save_reusable_rift_plan` | re.sub; plain | `markdown = re.sub(r"^# .*?$", f"# {title}", markdown, count=1, flags=re.MULTILINE)` | |
| `application/reusable_plans.py:355` | `load_reusable_campaign_plan` | re.fullmatch; plain | `if re.fullmatch(r"campaign-plan-reusable-[0-9a-f]{24}-[0-9a-f]{16}", artifact_id) is None:` | |
| `application/reusable_plans.py:415` | `load_reusable_campaign_plan` | re.fullmatch; plain | `if not isinstance(raw_snapshot_sha256, str) or re.fullmatch( r"[0-9a-f]{64}", raw_snapshot_sha256 ) is None:` | |
| `application/reusable_plans.py:542` | `delete_reusable_campaign_plan` | re.fullmatch; plain | `if re.fullmatch(r"campaign-plan-reusable-[0-9a-f]{24}-[0-9a-f]{16}", artifact_id) is None:` | |
| `application/reusable_plans.py:544` | `delete_reusable_campaign_plan` | re.fullmatch; plain | `if re.fullmatch(r"[0-9a-f]{64}", expected_content_sha256) is None:` | |
| `builder/_interview_extractor.py:289` | `InterviewExtractor._parse_and_validate` | re.search; plain | `match = re.search(r''''(?:json)?\s*([\s\S]*?)\s*'''', content)` | |
| `campaign/_folder.py:25` | `<module>` | re.compile; plain | `_CAMPAIGN_ID_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")` | |
| `campaign/_folder.py:266` | `_derive_campaign_slug` | re.sub; plain | `slug = re.sub(r"[^a-z0-9]+", "-", slug)` | |
| `domain/_content_schema_campaign_export.py:9` | `<module>` | JSON Schema pattern; plain | `_SHA = {"type": "string", "pattern": "^[0-9a-f]{64}$"}` | `sylvia-domain` content_vectors |
| `domain/_content_schema_character.py:105` | `<module>` | JSON Schema pattern; NEEDS fancy-regex: lookahead | `"pattern": r"^(?![\s\S]*\u0000)\S(?:[\s\S]*\S)?$(?![\s\S])",` | `sylvia-domain` content_vectors |
| `domain/_content_schema_character.py:107` | `<module>` | JSON Schema pattern; NEEDS fancy-regex: lookahead | `"sha256": {"type": "string", "pattern": r"^[0-9a-f]{64}$(?![\s\S])"},` | `sylvia-domain` content_vectors |
| `domain/_content_schema_character_profile.py:21` | `<module>` | JSON Schema pattern; NEEDS fancy-regex: lookahead | `"pattern": r"^(?![\s\S]*\u0000)\S(?:[\s\S]*\S)?$(?![\s\S])",` | `sylvia-domain` content_vectors |
| `domain/_content_schema_character_profile_v2.py:3` | `<module>` | JSON Schema pattern; plain | `TEXT = {"type": "string", "maxLength": 65536, "pattern": r"^[^\u0000]*$"}` | `sylvia-domain` content_vectors |
| `domain/_content_schema_character_profile_v2.py:5` | `<module>` | JSON Schema pattern; plain | `ID = {"type": "string", "minLength": 1, "maxLength": 160, "pattern": r"^[A-Za-z0-9_.:-]+$"}` | `sylvia-domain` content_vectors |
| `domain/_content_schema_character_profile_v2.py:59` | `<module>` | JSON Schema pattern; NEEDS fancy-regex: lookahead | `"pattern": r"^(?![\s\S]*\u0000)\S(?:[\s\S]*\S)?$(?![\s\S])",` | `sylvia-domain` content_vectors |
| `domain/_content_schema_character_profile_v2.py:126` | `<module>` | JSON Schema pattern; plain | `"pattern": r"^(?:https?://\|card://\|asset:sha256:)[^\u0000\s]+$",` | `sylvia-domain` content_vectors |
| `domain/_content_schema_character_profile_v2.py:137` | `<module>` | JSON Schema pattern; plain | `"sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},` | `sylvia-domain` content_vectors |
| `domain/_content_schema_character_profile_v2.py:151` | `<module>` | JSON Schema pattern; plain | `"propertyNames": {"type": "string", "maxLength": 256, "pattern": r"^[^\u0000]*$"},` | `sylvia-domain` content_vectors |
| `domain/_content_schema_declarations.py:8` | `<module>` | JSON Schema pattern; plain | `_SHA = {"type": "string", "pattern": "^[0-9a-f]{64}$"}` | `sylvia-domain` content_vectors |
| `domain/_content_schema_map_card.py:494` | `<module>` | JSON Schema pattern; NEEDS fancy-regex: lookahead | `"pattern": r"^(?=\S(?:[\s\S]*\S)?$)(?![\s\S]*\x00)[\s\S]+$",` | `sylvia-domain` content_vectors |
| `domain/_content_schema_map_card.py:621` | `<module>` | JSON Schema pattern; plain | `"pattern": r"[\s\S]*\S[\s\S]*",` | `sylvia-domain` content_vectors |
| `domain/_content_schema_map_card.py:627` | `<module>` | JSON Schema pattern; plain | `"pattern": r"[\s\S]*\S[\s\S]*",` | `sylvia-domain` content_vectors |
| `domain/_content_schema_map_card.py:660` | `<module>` | JSON Schema pattern; plain | `"pattern": r"[\s\S]*\S[\s\S]*",` | `sylvia-domain` content_vectors |
| `domain/_content_schema_map_card.py:699` | `<module>` | JSON Schema pattern; plain | `"pattern": r"[\s\S]*\S[\s\S]*",` | `sylvia-domain` content_vectors |
| `domain/_content_schema_package.py:17` | `<module>` | JSON Schema pattern; plain | `_SHA = {"type": "string", "pattern": "^[0-9a-f]{64}$"}` | `sylvia-domain` content_vectors |
| `domain/_content_schema_package_subformats.py:8` | `<module>` | JSON Schema pattern; plain | `_NONBLANK = {"type": "string", "minLength": 1, "pattern": r".*\S.*"}` | `sylvia-domain` content_vectors |
| `domain/_content_schema_package_subformats.py:9` | `<module>` | JSON Schema pattern; plain | `_SHA = {"type": "string", "pattern": "^[0-9a-f]{64}$"}` | `sylvia-domain` content_vectors |
| `domain/_content_schema_package_subformats.py:203` | `<module>` | JSON Schema pattern; plain | `"pattern": "^campaign-plan-reusable-[0-9a-f]{24}-[0-9a-f]{16}$",` | `sylvia-domain` content_vectors |
| `domain/_content_schema_package_subformats.py:213` | `<module>` | JSON Schema pattern; plain | `{"type": "string", "minLength": 1, "pattern": "^[^\\u0000]+$"},` | `sylvia-domain` content_vectors |
| `domain/_content_schema_panel.py:7` | `<module>` | JSON Schema pattern; NEEDS fancy-regex: lookahead | `IDENTITY = {"type": "string", "minLength": 1, "pattern": r"^\S(?:[\s\S]*\S)?$(?![\s\S])"}` | `sylvia-domain` content_vectors |
| `domain/_content_schema_panel.py:185` | `<module>` | JSON Schema pattern; plain | `"pattern": "^[A-Za-z_][A-Za-z0-9_]*$",` | `sylvia-domain` content_vectors |
| `domain/_content_schema_panel.py:190` | `<module>` | JSON Schema pattern; plain | `"pattern": "^[A-Za-z_][A-Za-z0-9_]*$",` | `sylvia-domain` content_vectors |
| `domain/_content_schema_plan.py:228` | `<module>` | JSON Schema pattern; NEEDS fancy-regex: lookahead | `"pattern": r"^(?=\S(?:[\s\S]*\S)?$)(?![\s\S]*\x00)[\s\S]+$",` | `sylvia-domain` content_vectors |
| `domain/_content_schema_plan.py:255` | `<module>` | JSON Schema pattern; plain | `"pattern": "^[0-9a-f]{64}$",` | `sylvia-domain` content_vectors |
| `domain/_content_schema_ruleset.py:16` | `<module>` | JSON Schema pattern; NEEDS fancy-regex: lookahead | `TEMPLATE = {"type": "string", "pattern": r"^\{[^{}]+\}$(?![\s\S])"}` | `sylvia-domain` content_vectors |
| `domain/_content_schema_ruleset.py:103` | `<module>` | JSON Schema pattern; plain | `"nonempty": {"type": "string", "minLength": 1, "pattern": r".*\S.*"},` | `sylvia-domain` content_vectors |
| `domain/_content_schema_ruleset.py:359` | `<module>` | JSON Schema pattern; NEEDS fancy-regex: lookahead | `r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$(?![\s\S])"` | `sylvia-domain` content_vectors |
| `domain/_content_schema_runtime.py:106` | `<module>` | JSON Schema pattern; plain | `_PARAMETER, {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},` | `sylvia-domain` content_vectors |
| `domain/_content_schema_runtime.py:140` | `<module>` | JSON Schema pattern; plain | `"trigger": {"type": "string", "pattern": "^on_[a-z][a-z0-9_]*$"},` | `sylvia-domain` content_vectors |
| `domain/_content_schema_runtime.py:170` | `<module>` | JSON Schema pattern; plain | `"propertyNames": {"pattern": "^[a-z][a-z0-9_]*$"},` | `sylvia-domain` content_vectors |
| `domain/_content_schema_ui.py:28` | `trimmed_text` | JSON Schema pattern; NEEDS fancy-regex: lookahead | `"pattern": r"^\S(?:[^\u0000]*\S)?$(?![\s\S])",` | `sylvia-domain` content_vectors |
| `domain/_content_schema_ui.py:29` | `trimmed_text` | JSON Schema pattern; plain | `"not": {"pattern": r"\u0000"},` | `sylvia-domain` content_vectors |
| `domain/_content_schema_ui.py:37` | `<module>` | JSON Schema pattern; NEEDS fancy-regex: lookahead | `"pattern": r"^[A-Za-z0-9][A-Za-z0-9._:-]*$(?![\s\S])",` | `sylvia-domain` content_vectors |
| `domain/_content_schema_ui.py:39` | `<module>` | JSON Schema pattern; NEEDS fancy-regex: lookahead | `_LAYOUT_ID = {"type": "string", "pattern": r"^[A-Za-z0-9_-]{1,96}$(?![\s\S])"}` | `sylvia-domain` content_vectors |
| `domain/authority.py:586` | `_is_reserved_canonical_channel` | re.sub; NEEDS fancy-regex: lookahead, lookbehind | `separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)` | `sylvia-domain` identity_vectors |
| `domain/authority.py:587` | `_is_reserved_canonical_channel` | re.split; plain | `tokens = tuple(token for token in re.split(r"[^a-z0-9]+", separated.casefold()) if token)` | `sylvia-domain` identity_vectors |
| `domain/campaign_plan.py:26` | `<module>` | re.compile; plain | `_ABSOLUTE_PATH = re.compile(r"(?:^[A-Za-z]:[\\/]\|^/\|^\\\\)")` || `sylvia-domain` campaign_plan_vectors |
| `domain/campaign_plan.py:129` | `CampaignPlanFieldsError.__init__` | re.fullmatch; plain | `if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name)` || `sylvia-domain` campaign_plan_vectors |
| `domain/campaign_plan.py:209` | `_identity` | re.fullmatch; plain | `if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", text):` || `sylvia-domain` campaign_plan_vectors |
| `domain/campaign_plan.py:258` | `_validate_party_placements` | re.fullmatch; plain | `if not re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*", state.key):` || `sylvia-domain` campaign_plan_vectors |
| `domain/campaign_plan.py:274` | `_package_binding` | re.fullmatch; plain | `if not re.fullmatch(r"[0-9a-f]{64}", content_sha256):` || `sylvia-domain` campaign_plan_vectors |
| `domain/content_validation.py:54` | `_field_path` | re.fullmatch; plain | `elif isinstance(part, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,95}", part):` | `sylvia-domain` content_vectors |
| `domain/content_validation.py:115` | `validate_content_shape` | re.search; pattern built at run time, check by hand | `and not any(re.search(pattern, field) for pattern in patterns)` | `sylvia-domain` content_vectors (no shipped schema has `patternProperties`; the engine refuses it) |
| `domain/package_library.py:757` | `validate_portable_path` | re.match; plain | `if re.match(r"^[A-Za-z]:", path):` | `sylvia-domain` identity_vectors |
| `domain/starting_state.py:14` | `<module>` | re.compile; plain | `_BINDING = re.compile(r"\$[a-zA-Z_][a-zA-Z0-9_]{0,63}\Z")` | `sylvia-domain` manifest_vectors |
| `state/_validators.py:133` | `_validate_spell_slots` | re.compile; pattern built at run time, check by hand | `_re = re.compile(_SPELL_SLOT_KEY_RE)` | |
| `state/_validators.py:305` | `validate_entity_id` | re.match; plain | `if not re.match(r"^[a-z0-9_-]+$", entity_id):` | |

## Lane 2: Game engine

| Where | Function | Kind | Code | Done |
| --- | --- | --- | --- | --- |
| `application/package_runtime.py:279` | `AuthorityMemoryStateBridge.create_entity` | re.fullmatch; plain | `or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", entity_id) is None` | |
| `application/package_runtime.py:610` | `AuthorityMemoryStateBridge.spatial_private_variables` | re.findall; plain | `private.intersection(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expression))` | |
| `application/package_runtime.py:612` | `AuthorityMemoryStateBridge.spatial_private_variables` | re.search; pattern built at run time, check by hand | `re.search(r"(?<![\w.])" + re.escape(path) + r"(?=$\|[.\s\[])", expression)` | |
| `application/package_runtime.py:1365` | `<module>` | re.compile; plain | `_SAFE_REFERENCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}")` | |
| `application/player_actions.py:19` | `<module>` | re.compile; plain | `_LOCAL_NAME = re.compile(r"^_[A-Za-z][A-Za-z0-9_]{0,63}$")` | |
| `application/player_actions.py:140` | `parse_player_controls` | re.fullmatch; plain | `or any(not isinstance(name, str) or re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', name) is None` | |
| `application/player_actions.py:147` | `parse_player_controls` | re.fullmatch; plain | `or re.fullmatch(r'state\.[A-Za-z_][A-Za-z0-9_.]{0,127}', preflight_path["scene"]) is None` | |
| `application/shot_effects.py:24` | `<module>` | re.compile; plain | `_HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}\Z")` | |
| `ruleset/schema.py:38` | `Meta.id_must_be_kebab_case` | re.match; plain | `if not re.match(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", v):` | |
| `runtime/_effect_executor.py:69` | `_handle_shuffle` | re.fullmatch; plain | `if not isinstance(destination, str) or re.fullmatch(r"_[A-Za-z0-9_]+", destination) is None:` | |
| `runtime/_effect_executor.py:367` | `_handle_request_choice` | re.fullmatch; plain | `if isinstance(raw_options, str) and re.fullmatch(r"_[A-Za-z0-9_]+", raw_options):` | |
| `runtime/_effect_executor.py:390` | `_handle_request_choice` | re.fullmatch; plain | `or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", option_id) is None` | |
| `runtime/_effect_executor.py:2151` | `_resolve_value` | re.match; plain | `match = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)", expr)` | |
| `runtime/_expression_parser.py:156` | `<module>` | re.compile; pattern built at run time, check by hand | `_TOKEN_PATTERN = re.compile(_TOKEN_REGEX)` | yes: `expression_parse_vectors` |
| `runtime/_frontmatter.py:18` | `<module>` | re.compile; plain | `_FRONTMATTER_RE = re.compile( r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL )` | |
| `runtime/_parameter_declarations.py:84` | `resolve_parameter_declarations` | re.fullmatch; pattern built at run time, check by hand | `if not isinstance(name, str) or not re.fullmatch(PARAMETER_DECLARATION_ID_PATTERN, name):` | |
| `runtime/_scene_awareness.py:359` | `SceneAwarenessBuilder._read_faction_cs` | re.compile; pattern built at run time, check by hand | `pattern = re.compile( rf"(?:^\|\n)#{{1,3}}\s+[^\n]*{re.escape(faction_name)}[^\n]*\n" rf"((?:(?!\n#{{1,3}}\s).\|\n)*)", re.IGNORECASE, )` | |
| `runtime/_scene_awareness.py:423` | `_parse_location_md` | re.match; plain | `heading_match = re.match(r"^#\s+(.+)$", body, re.MULTILINE)` | |
| `runtime/_scene_awareness.py:428` | `_parse_location_md` | re.search; plain | `type_match = re.search(r"\*\*Type:\*\*\s*(.+)$", body, re.MULTILINE)` | |
| `runtime/_scene_awareness.py:433` | `_parse_location_md` | re.search; plain | `region_match = re.search(r"\*\*Region:\*\*\s*(.+)$", body, re.MULTILINE)` | |
| `runtime/_scene_awareness.py:438` | `_parse_location_md` | re.search; NEEDS fancy-regex: lookahead | `overview_match = re.search( r"^##\s+Overview\s*\n\n((?:(?!^##\s).\|\n)+?)(?=\n##\s\|\Z)", body, re.MULTILINE, )` | |
| `runtime/_scene_awareness.py:448` | `_parse_location_md` | re.sub; plain | `first_para = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", first_para)` | |
| `runtime/_scene_awareness.py:452` | `_parse_location_md` | re.search; NEEDS fancy-regex: lookahead | `atmos_match = re.search( r"^##\s+Atmosphere\s*\n\n((?:(?!^##\s).\|\n)+?)(?=\n##\s\|\Z)", body, re.MULTILINE, )` | |
| `runtime/_scene_awareness.py:483` | `_parse_npc_md` | re.match; plain | `heading_match = re.match(r"^#\s+(.+)$", body, re.MULTILINE)` | |
| `runtime/_scene_awareness.py:488` | `_parse_npc_md` | re.search; NEEDS fancy-regex: lookahead | `pers_match = re.search( r"^##\s+Personality\s*\n\n((?:(?!^##\s).\|\n)+?)(?=\n##\s\|\Z)", body, re.MULTILINE, )` | |
| `runtime/_scene_awareness.py:496` | `_parse_npc_md` | re.split; NEEDS fancy-regex: lookbehind | `first_sentence = re.split(r"(?<=[.!?])\s+", pers_text)[0].strip()` | |
| `runtime/_schema_validator.py:103` | `<module>` | re.compile; plain | `_SPATIAL_TEMPLATE = re.compile(r"^\{[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\}$")` | |
| `runtime/_schema_validator.py:150` | `<module>` | re.compile; plain | `VALID_TRIGGER_PATTERN: re.Pattern[str] = re.compile(r"^on_[a-z][a-z0-9_]*$")` | |
| `runtime/_schema_validator.py:199` | `validate_ruleset_data` | re.match; plain | `elif not re.match(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", str(meta["id"])):` | |
| `runtime/_schema_validator.py:511` | `_validate_definition_row_schema` | re.fullmatch; plain | `if not isinstance(discriminator, str) or not re.fullmatch( r"[A-Za-z_][A-Za-z0-9_]*", discriminator ):` | |
| `runtime/_schema_validator.py:610` | `_validate_definition_lookups` | re.fullmatch; plain | `if not isinstance(store_as, str) or not re.fullmatch(r"_[A-Za-z0-9_]+", store_as):` | |
| `runtime/_schema_validator.py:681` | `_validate_tools` | re.match; plain | `if not re.match(r"^[a-z_][a-z0-9_]*$", tool_name):` | |
| `runtime/_schema_validator.py:825` | `_validate_model_decision_boundary` | re.fullmatch; plain | `or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", control["actor_participant_field"]) is None` | |
| `runtime/_schema_validator.py:858` | `_validate_model_decision_boundary` | re.fullmatch; plain | `or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", part) is None` | |
| `runtime/_schema_validator.py:1204` | `_validate_spatial_mutation_effect` | re.fullmatch; plain | `or re.fullmatch(r"state\.[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", scene)` | |
| `runtime/_schema_validator.py:1215` | `_validate_spatial_mutation_effect` | re.fullmatch; plain | `if not isinstance(store_as, str) or re.fullmatch(r"_[A-Za-z0-9_]+", store_as) is None:` | |
| `runtime/_schema_validator.py:1264` | `_validate_spatial_query_effect` | re.fullmatch; plain | `or re.fullmatch(r"state\.[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", scene)` | |
| `runtime/_schema_validator.py:1269` | `_validate_spatial_query_effect` | re.fullmatch; plain | `if not isinstance(store_as, str) or re.fullmatch(r"_[A-Za-z0-9_]+", store_as) is None:` | |
| `runtime/_schema_validator.py:1607` | `_validate_request_choice_effect` | re.fullmatch; plain | `or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", choice_id) is None` | |
| `runtime/_schema_validator.py:1615` | `_validate_request_choice_effect` | re.fullmatch; plain | `if isinstance(options, str) and re.fullmatch(r"_[A-Za-z0-9_]+", options):` | |
| `runtime/_schema_validator.py:1629` | `_validate_request_choice_effect` | re.fullmatch; plain | `or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", option_id) is None` | |
| `runtime/_template_resolver.py:19` | `<module>` | re.compile; plain | `_TEMPLATE_RE: re.Pattern[str] = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)\}")  # noqa: E501` | |
| `runtime/_template_resolver.py:67` | `resolve_params` | re.fullmatch; plain | `match = re.fullmatch(r"\{([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)\}", value)  # noqa: E501` | |
| `runtime/_time_manager.py:55` | `<module>` | re.compile; plain | `_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s+(\w+)\s*$")` | |
| `spatial/_interface.py:17` | `<module>` | re.compile; plain | `_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")` | |

## Lane 3: Storage and authority

| Where | Function | Kind | Code | Done |
| --- | --- | --- | --- | --- |
| `application/authority.py:308` | `_markdown_sections` | re.match; plain | `match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)` | |
| `application/authority.py:319` | `_markdown_sections` | re.sub; plain | `slug = re.sub(r"[^a-z0-9]+", "-", heading.casefold()).strip("-")` | |
| `application/authority.py:327` | `_select_markdown_section` | re.sub; plain | `normalized = re.sub(r"[^a-z0-9]+", "-", fragment.casefold()).strip("-")` | |
| `application/authority.py:6262` | `AuthorityOwner.read_campaign_reference` | re.fullmatch; plain | `match = re.fullmatch(r"adventures/([1-9][0-9]*)/(structured(?:/.*)?\|markdown)", path)` | |
| `application/authority.py:6313` | `AuthorityOwner.read_campaign_reference` | re.fullmatch; plain | `if not isinstance(readable, str) or not re.fullmatch(r"[0-9]{1,10}", page):` | |
| `application/authority.py:6443` | `AuthorityOwner.read_campaign_reference` | re.fullmatch; plain | `if re.fullmatch(r"text/(0\|[1-9][0-9]{0,9})", fragment) is None:` | |
| `application/knowledge_search.py:101` | `knowledge_terms` | re.findall; plain | `words = re.findall(r"[^\W_]+(?:['’][^\W_]+)?", value.casefold())` | |
| `application/ruleless_play.py:83` | `<module>` | re.compile; NEEDS fancy-regex: lookahead, lookbehind | `re.compile(r"(?<![\w-])\d{0,3}d(?:4\|6\|8\|10\|12\|20\|100)(?![\w-])", re.IGNORECASE),` | |
| `application/ruleless_play.py:87` | `<module>` | re.compile; plain | `re.compile( r"\broll(?:ing)?\s+(?:for\b\|initiative\b\|to\s+(?:see\|hit\|save\|resist)\b" r"\|(?:a\|an\|your)\s+(?:[a-z]+\s+){0,2}(?:check\|save\|r...` | |
| `application/ruleless_play.py:95` | `<module>` | re.compile; pattern built at run time, check by hand | `re.compile( r"\b(?:(?:" + _SKILL_WORDS + r")\s+(?:check\|save\|saving\s+throw\|test\|roll)s?" r"\|saving\s+throws?)\b", re.IGNORECASE, ),` | |
| `application/ruleless_play.py:103` | `<module>` | re.compile; pattern built at run time, check by hand | `re.compile( # Only a bare, skill-named or difficulty-named check or save is a rules # request; "make a quick check of the windows" or "make a save ...` | |
| `application/ruleless_play.py:115` | `<module>` | re.compile; plain | `re.compile(r"\b(?:DC\s*\d+\|difficulty\s+class\|target\s+number\s+\d+)\b", re.IGNORECASE),` | |
| `application/ruleless_play.py:119` | `<module>` | re.compile; plain | `re.compile(r"\b(?:armou?r\s+class\|AC\s*\d+)\b", re.IGNORECASE),` | |
| `application/ruleless_play.py:123` | `<module>` | re.compile; plain | `re.compile(r"\b(?:hit\s+points?\|health\s+points?\|HP)\b", re.IGNORECASE),` | |
| `application/ruleless_play.py:127` | `<module>` | re.compile; plain | `re.compile( r"\b\d+\s+(?:points?\s+of\s+)?(?:[a-z]+\s+)?damage\b", re.IGNORECASE, ),` | |
| `application/ruleless_play.py:133` | `<module>` | re.compile; plain | `re.compile( r"\b(?:initiative\s+(?:order\|roll\|count\|score)\|roll(?:s\|ed)?\s+initiative" r"\|combat\s+(?:mode\|round)s?\|round\s+\d+\s+of\s+comb...` | |
| `application/ruleless_play.py:141` | `<module>` | re.compile; plain | `re.compile( r"\b(?:attack\s+rolls?\|to-hit\|bonus\s+actions?\|spell\s+slots?" r"\|experience\s+points\|\d+\s*XP\|level(?:s\|ed)?\s+up)\b", re.IGNOR...` | |
| `application/ruleless_play.py:149` | `<module>` | re.compile; NEEDS fancy-regex: lookahead | `re.compile( r"\b(?:with\|at\|has\|have\|gains?\|grants?\|roll\s+with)\s+(?:dis)?advantage\b" r"(?!\s+(?:of\|over\|to\|in)\b)", re.IGNORECASE, ),` | |
| `application/ruleless_play.py:158` | `<module>` | re.compile; plain | `_NEGATION = re.compile(r"\b(?:no\|not\|never\|without\|nor\|instead\s+of)\b\|n't\b", re.IGNORECASE)` | |
| `infrastructure/campaign_archive.py:815` | `_valid_provenance_name` | re.fullmatch; plain | `return isinstance(value, str) and re.fullmatch(r"[a-z0-9][a-z0-9_]{0,127}", value) is not None` | |
| `infrastructure/campaign_archive.py:819` | `_valid_sha256` | re.fullmatch; plain | `return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None` | |
| `infrastructure/campaign_archive.py:1001` | `_safe_member_name` | re.match; plain | `return not bool(re.match(r"^[A-Za-z]:", name))` | |
| `infrastructure/campaign_archive.py:2610` | `validate_campaign_archive` | re.fullmatch; plain | `if any(not re.fullmatch(r"blobs/[0-9a-f]{64}", name) for name in blob_names):` | |
| `infrastructure/safe_file.py:95` | `<module>` | re.compile; plain | `_MOUNTINFO_ESCAPE = re.compile(r"\\([0-7]{3})")` | |

## Lane 4: Providers and agents

| Where | Function | Kind | Code | Done |
| --- | --- | --- | --- | --- |
| `agent/_activity.py:13` | `operation_category` | re.sub; plain | `name = re.sub(r"^tool_[a-f0-9]{12}_", "", name or "")` | |
| `agent/_character_draft_tool.py:49` | `character_draft_tool.open_draft` | re.fullmatch; plain | `or re.fullmatch(r"participant-[0-9a-f]{24}-[0-9a-f]{16}", params["artifact_id"]) is None` | |
| `agent/_character_draft_tool.py:50` | `character_draft_tool.open_draft` | re.fullmatch; plain | `or re.fullmatch(r"[0-9a-f]{64}", params["expected_content_sha256"]) is None` | |
| `agent/_character_reuse_tool.py:53` | `reuse_character_copy_tool.copy` | re.fullmatch; plain | `or re.fullmatch(r"participant-[0-9a-f]{24}-[0-9a-f]{16}", params["artifact_id"]) is None` | |
| `agent/_character_reuse_tool.py:55` | `reuse_character_copy_tool.copy` | re.fullmatch; plain | `or re.fullmatch(r"[0-9a-f]{64}", params["expected_content_sha256"]) is None` | |
| `agent/_participant_file_tool.py:33` | `participant_file_tool.use_file` | re.fullmatch; plain | `or re.fullmatch(r"[0-9a-f]{64}", params["expected_version"]) is None` | |
| `agent/_participant_file_tool.py:78` | `participant_file_tool` | JSON Schema pattern; plain | `"expected_version": {"type": "string", "pattern": "^[0-9a-f]{64}$"},` | |
| `agent/_provider_session.py:411` | `_package_tool_alias` | re.sub; plain | `slug = re.sub(r"[^A-Za-z0-9_]", "_", name).strip("_") or "tool"` | |
| `agent/_provider_session.py:894` | `_normalize_state_references.key` | re.sub; plain | `return re.sub(r"[\s_-]+", "_", re.sub(r"['’]", "", value.casefold())).strip("_")` | |
| `agent/_provider_session.py:1290` | `_speaker_names_overlap.contains` | re.search; pattern built at run time, check by hand | `return re.search(rf"(?<!\w){re.escape(inner)}(?!\w)", outer) is not None` | |
| `agent/_provider_session.py:2369` | `ProviderSessionAdapter._relevant_character_files_message` | re.search; pattern built at run time, check by hand | `if re.search( rf"(?<!\w){re.escape(alias)}(?!\w)", current_player_text, flags=re.IGNORECASE, )` | |
| `agent/_ruleset_test_runner.py:50` | `_failure_message` | re.fullmatch; plain | `if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code):` | |
| `agent/_runner.py:2015` | `AgentRunner._compress_chapter` | re.sub; plain | `clean = re.sub(r"^'''(?:json)?\s*", "", clean)` | |
| `agent/_runner.py:2016` | `AgentRunner._compress_chapter` | re.sub; plain | `clean = re.sub(r"\s*'''$", "", clean)` | |
| `agent/_stateless_mcp.py:1341` | `_safe_error` | re.findall; pattern built at run time, check by hand | `for match in re.findall(pattern, str(error))` | |
| `agent/_tool_loader.py:79` | `<module>` | re.compile; plain | `_ALIAS = re.compile(r"^tool_[0-9a-f]{12}_(.+)$")` | |
| `agent/_working_context.py:579` | `WorkingContextProvider.read_history` | re.compile; pattern built at run time, check by hand | `match = re.compile(re.escape(query), re.IGNORECASE).search(text, offset)` | |
| `application/character_chat.py:37` | `<module>` | re.compile; plain | `CHAT_ID = re.compile(r"character-chat-[0-9a-f]{24}")` | |
| `application/character_chat.py:48` | `<module>` | re.compile; plain | `_MACRO = re.compile(r"\{\{\s*(char\|user)\s*\}\}\|<(BOT\|USER)>", re.IGNORECASE)` | |
| `application/character_chat.py:49` | `<module>` | re.compile; plain | `_SHA256 = re.compile(r"[0-9a-f]{64}")` | |
| `config/_credential_protection.py:83` | `<module>` | re.compile; plain | `_TEMPORARY_NAME = re.compile(r"\.credential-key-[0-9a-f]{16}\.tmp")` | |
| `config/_resolver.py:18` | `<module>` | re.compile; NEEDS fancy-regex: lookbehind | `_VAR_PATTERN = re.compile(r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*)\}")` | |
| `config/claude_settings.py:178` | `claude_settings_action` | re.fullmatch; plain | `or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._\[\]-]{0,127}", model["id"])` | |
| `config/provider_settings.py:44` | `<module>` | re.compile; NEEDS fancy-regex: atomic/possessive | `_HEADER_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_'\|~-]{1,80}$")` | |
| `config/provider_settings.py:45` | `<module>` | re.compile; plain | `_PROFILE_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")` | |
| `config/provider_settings.py:47` | `<module>` | re.compile; plain | `_NATIVE_SETTING = re.compile(r"[a-z][a-z0-9_-]{0,31}")` | |
| `provider/_claude.py:118` | `prepare_request` | re.fullmatch; plain | `if not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) is None:` | |
| `provider/_claude_install.py:188` | `_version` | re.fullmatch; plain | `match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", text.strip())` | |
| `provider/_claude_install.py:334` | `ClaudeInstaller._install` | re.fullmatch; plain | `or re.fullmatch(r"[0-9a-f]{64}", checksum) is None` | |
| `provider/_claude_native.py:38` | `<module>` | re.compile; plain | `_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)` | |
| `provider/_claude_runtime.py:238` | `find_runtime` | re.fullmatch; plain | `if re.fullmatch(r"\d+\.\d+\.\d+", entry.name) and candidate.is_file():` | |
| `provider/_claude_runtime.py:260` | `runtime_version` | re.fullmatch; plain | `match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+) \(Claude Code\)\s*", result.stdout)` | |
| `provider/_model_facts.py:145` | `claude_model_id` | re.sub; plain | `bare = re.sub(r"[-@]\d{8}$", "", model_id.strip().casefold().rsplit("/", 1)[-1])` | |
| `provider/_reasoning.py:43` | `reasoning_values` | re.fullmatch; plain | `not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", value)` | |
| `provider/_speed.py:22` | `<module>` | re.compile; plain | `_TIER_ID = re.compile(r"[a-z][a-z0-9_-]{0,31}")` | |
| `session/_divergence.py:109` | `DivergenceDetector.analyze` | re.search; pattern built at run time, check by hand | `if re.search(pattern, response_lower):` | |
| `session/_divergence.py:123` | `DivergenceDetector.analyze` | re.search; pattern built at run time, check by hand | `if re.search(pattern, response_lower):` | |
| `session/_narrative_scanner.py:88` | `NarrativeScanner._compile_rules._c` | re.compile; pattern built at run time, check by hand | `return re.compile(pattern, flags)` | |
| `session/_narrative_scanner.py:210` | `NarrativeScanner.scan` | re.compile; pattern built at run time, check by hand | `re.compile(tag, re.IGNORECASE \| re.DOTALL))` | |
| `tools/_builder_handlers.py:274` | `tool_search_files` | re.compile; pattern built at run time, check by hand | `regex = re.compile(pattern)` | |

## Lane 5: Owner, clients and release

| Where | Function | Kind | Code | Done |
| --- | --- | --- | --- | --- |
| `application/desktop_update/helper.py:277` | `_failure_code` | re.fullmatch; plain | `if not isinstance(code, str) or re.fullmatch(r"[a-z_]{1,64}", code) is None:` | |
| `application/desktop_update/helper.py:323` | `main` | re.fullmatch; plain | `or re.fullmatch(r"[0-9a-f]{32}", row["restart_nonce"]) is None` | |
| `application/desktop_update/manifest.py:22` | `<module>` | re.compile; plain | `_HASH = re.compile(r"[0-9a-f]{64}\Z")` | `update_vectors` |
| `application/desktop_update/manifest.py:23` | `<module>` | re.compile; plain | `_VERSION = re.compile( r"(0\|[1-9][0-9]{0,8})\.(0\|[1-9][0-9]{0,8})\.(0\|[1-9][0-9]{0,8})" r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?" r"(?:\+([0-...` | `update_vectors` |
| `application/desktop_update/manifest.py:209` | `release_url` | re.search; NEEDS fancy-regex: lookahead | `or re.search(r"%(?![0-9A-Fa-f]{2})", parsed.path)` | `update_vectors` |
| `application/desktop_update/manifest.py:237` | `payload_path` | re.fullmatch; plain | `re.fullmatch(r"[A-Za-z0-9_. -]+", part) is None or part.endswith((".", " "))` | `update_vectors` |
| `application/desktop_update/manifest.py:346` | `_parse_manifest` | re.fullmatch; plain | `or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key_id) is None` | `update_vectors` |
| `application/desktop_update/storage.py:204` | `operation_id` | re.fullmatch; plain | `if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{32}", value) is None:` | `local-services`, `sylvia-owner` storage tests |
| `application/desktop_update/storage.py:343` | `UpdateStore._move_update` | re.fullmatch; plain | `error_code is not None and re.fullmatch(r"[a-z_]{1,64}", error_code) is None` | |
| `application/desktop_update/storage.py:375` | `UpdateStore.load` | re.fullmatch; plain | `or re.fullmatch(r"[a-z_]{1,64}", row["error_code"]) is None` | |
| `application/desktop_update/transport.py:44` | `official_request` | re.search; NEEDS fancy-regex: lookahead | `or re.search(r"%(?![0-9A-Fa-f]{2})", parsed.path)` | `update_vectors` |
| `interfaces/owner_process.py:301` | `<module>` | re.compile; plain | `_SEMANTIC_VERSION = re.compile( r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$" )` | |
| `interfaces/owner_process.py:954` | `_required_artifact_id` | re.fullmatch; plain | `if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value) is None:` | |
| `interfaces/webui.py:80` | `RemoteAccess.from_file` | re.fullmatch; plain | `if not re.fullmatch( r"https://(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.){2,}ts\.net" r"(?::[1-9][0-9]{0,4})?", origin, ):` | |
| `interfaces/webui.py:101` | `RemoteAccess.from_file` | re.fullmatch; pattern built at run time, check by hand | `matched = re.fullmatch(rb"([A-Za-z0-9_-]{43,128})(?:\r?\n)?", raw)` | |
| `interfaces/webui.py:300` | `browser_command` | re.sub; NEEDS fancy-regex: lookahead, lookbehind | `params = {re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower(): value for key, value in raw.items()}` | |
| `interfaces/webui.py:321` | `LocalWebUI.__init__` | re.fullmatch; plain | `if remote is None and not re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}", origin):` | |
| `interfaces/webui.py:439` | `LocalWebUI.login` | re.fullmatch; plain | `if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", key):` | |
| `interfaces/webui.py:520` | `LocalWebUI.command` | re.fullmatch; plain | `or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identity)` | |
| `interfaces/webui.py:622` | `LocalWebUI.export` | re.fullmatch; plain | `if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identity):` | |
| `release_artifact.py:51` | `<module>` | re.compile; plain | `_VERSION_RE = re.compile( r"^(0\|[1-9][0-9]*)\.(0\|[1-9][0-9]*)\.(0\|[1-9][0-9]*)(?:-alpha\.([1-9][0-9]*))?$" )` | |
| `release_artifact.py:54` | `<module>` | re.compile; plain | `_CHANNEL_RE = re.compile(r"^[a-z][a-z0-9]*$")` | |
| `release_artifact.py:55` | `<module>` | re.compile; pattern built at run time, check by hand | `_RUST_CHANNEL_RE = re.compile(rb'^const RELEASE_CHANNEL: &str = "([^"\r\n]*)";\r?$', re.MULTILINE)` | |
| `release_artifact.py:57` | `<module>` | re.compile; plain | `_GIT_ID_RE = re.compile(r"^[0-9a-f]{40}$")` | |
| `release_artifact.py:58` | `<module>` | re.compile; plain | `_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")` | |
| `release_artifact.py:59` | `<module>` | re.compile; plain | `_UTC_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")` | |
