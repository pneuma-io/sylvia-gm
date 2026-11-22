# Unicode-dependent calls

Python snapshot `0da1010feb5a520f917f5d69ff8553ddb174e9e7`. 185 sites. Case folding, normalisation and character-class calls. Results depend on the Unicode tables of the interpreter (CPython 3.11 has Unicode 14.0, 3.12 has 15.0). The reference environment for the port is CPython 3.12.11; pin the Rust tables to Unicode 15.0 and include non-ASCII names in the vectors.

Written by `docs/rust-port/tools/site_lists.py`. A worker ticks the Done column when the Rust side reproduces the site and a vector or comparison session proves it.

| Lane | Sites |
| --- | ---: |
| 1. Formats and content | 36 |
| 2. Game engine | 8 |
| 3. Storage and authority | 26 |
| 4. Providers and agents | 62 |
| 5. Owner, clients and release | 53 |

## Lane 1: Formats and content

| Where | Function | Kind | Code | Done |
| --- | --- | --- | --- | --- |
| `application/bundled_packages.py:147` | `_package_payload` | .casefold() | `if path.suffix.casefold() in _TEXT_PACKAGE_SUFFIXES:` |  `sylvia-app` bundled_vectors |
| `application/bundled_packages.py:476` | `bundled_rift_source_catalog` | .casefold() | `return tuple(path for path in values if not needle or needle in path.casefold())[:512]` |  `sylvia-app` bundled_vectors |
| `application/character_card_import.py:154` | `_asset_uri` | .isspace() | `if len(uri) > 4096 or "\x00" in uri or any(c.isspace() for c in uri):` | |
| `application/character_profiles.py:134` | `validate_character_profile` | .isspace() | `if any(character.isspace() for character in uri):` |  `sylvia-app` character_profile_vectors |
| `application/import_repair.py:132` | `_folded_portable_name` | unicodedata.normalize | `if unicodedata.normalize("NFC", name) != name:` | |
| `application/import_repair.py:134` | `_folded_portable_name` | .casefold() | `return name.casefold()` | |
| `application/library_sources.py:41` | `LibrarySourceReader.candidates` | .casefold() | `folded = query.casefold()` |  `sylvia-app` library_source_vectors |
| `application/package_materialization.py:161` | `materialize_package_archive` | .casefold() | `folded = path.casefold()` |  `sylvia-app` bundled_vectors |
| `builder/_package_materializer.py:176` | `_read_payloads` | .casefold() | `normalized = relative.casefold()` |  `sylvia-app` package_materializer_vectors |
| `builder/_package_record_edits.py:193` | `apply_package_record_edits` | .casefold() | `if len({name.casefold() for name in expected_paths}) != len(expected_paths):` |  `sylvia-app` package_record_edit_vectors |
| `builder/_plan_snapshot_materializer.py:179` | `_removed_paths` | .casefold() | `if len({item.casefold() for item in result}) != len(result):` | |
| `builder/_plan_snapshot_materializer.py:200` | `_apply_plan_file_edits` | .casefold() | `if {path.casefold() for path in removed} & {path.casefold() for path in changed}:` | |
| `domain/authority.py:587` | `_is_reserved_canonical_channel` | .casefold() | `tokens = tuple(token for token in re.split(r"[^a-z0-9]+", separated.casefold()) if token)` | `sylvia-domain` identity_vectors |
| `domain/campaign_plan.py:377` | `_scan_private` | .casefold() | `if key_text.casefold() in _PRIVATE_KEYS:` | `sylvia-domain` campaign_plan_vectors |
| `domain/package_library.py:2033` | `manifest_from_record` | .casefold() | `if len({file.path.casefold() for file in files}) != len(files):` | `sylvia-domain` manifest_vectors |
| `state/_exceptions.py:51` | `EntityNotFoundError.__init__` | .capitalize() | `super().__init__(f"{entity_type.capitalize()} {entity_id!r} not found")` | |
| `state/_exceptions.py:66` | `DuplicateEntityError.__init__` | .capitalize() | `f"{entity_type.capitalize()} {entity_id!r} already exists"` | |

## Lane 2: Game engine

| Where | Function | Kind | Code | Done |
| --- | --- | --- | --- | --- |
| `runtime/_scene_awareness.py:266` | `SceneAwarenessBuilder._build_single_npc` | .capitalize() | `role = kind.capitalize()` | |
| `runtime/_time_manager.py:215` | `advance_time` | .title() | `time_str = f"Day {day}, {time_of_day.title()}, {hour:02d}:{minute:02d}"` |  not reached: the package bridge has no `_ensure_campaign_entity`, so `advance_time`'s campaign-clock branch raises before it (#349) |
| `runtime/_time_manager.py:283` | `get_current_datestamp` | .title() | `main_parts.append(time_of_day.title())` |  not reached: only the retired `_state_memo` calls `get_current_datestamp` (#349) |
| `runtime/_time_manager.py:330` | `get_time_for_context` | .title() | `parts.append(time_of_day.title())` |  not reached: nothing calls `get_time_for_context` outside the `runtime/__init__.py` re-exports (#349) |

## Lane 3: Storage and authority

| Where | Function | Kind | Code | Done |
| --- | --- | --- | --- | --- |
| `application/authority.py:319` | `_markdown_sections` | .casefold() | `slug = re.sub(r"[^a-z0-9]+", "-", heading.casefold()).strip("-")` | |
| `application/authority.py:327` | `_select_markdown_section` | .casefold() | `normalized = re.sub(r"[^a-z0-9]+", "-", fragment.casefold()).strip("-")` | |
| `application/authority.py:356` | `_markdown_section_references` | .casefold() | `heading_key = heading.casefold().rstrip("s")` | |
| `application/authority.py:5394` | `AuthorityOwner.package_candidate_text` | .casefold() | `if notice_name not in {"license", "copyright"} and not portable_path.casefold().endswith(` | |
| `application/authority.py:5872` | `AuthorityOwner.search_campaign_knowledge` | .casefold() | `if Path(path).suffix.casefold() not in {` | |
| `application/authority.py:5967` | `AuthorityOwner.search_campaign_knowledge` | .casefold() | `heading_key = heading.casefold().rstrip("s")` | |
| `application/authority.py:6501` | `AuthorityOwner.read_campaign_reference` | .casefold() | `selected_heading.casefold().rstrip("s") if selected_heading is not None else ""` | |
| `application/knowledge_search.py:101` | `knowledge_terms` | .casefold() | `words = re.findall(r"[^\W_]+(?:['’][^\W_]+)?", value.casefold())` | |
| `infrastructure/builder_source_ingestion.py:143` | `_extract_source` | .casefold() | `extension = path.suffix.casefold()` | |
| `infrastructure/campaign_archive.py:2587` | `validate_campaign_archive` | .casefold() | `casefold_aliases = [name.casefold() for name in names]` | |
| `infrastructure/campaign_archive.py:2588` | `validate_campaign_archive` | unicodedata.normalize | `nfc_aliases = [unicodedata.normalize("NFC", name).casefold() for name in names]` | |
| `infrastructure/package_archive.py:146` | `_normalized_name` | unicodedata.normalize | `normalized = unicodedata.normalize("NFC", name)` | `sylvia-domain` archive_vectors |
| `infrastructure/package_archive.py:149` | `_normalized_name` | .casefold() | `return normalized.casefold()` | `sylvia-domain` archive_vectors |

## Lane 4: Providers and agents

| Where | Function | Kind | Code | Done |
| --- | --- | --- | --- | --- |
| `agent/_builder_session.py:436` | `_builder_reference_tool.inspect_page` | .casefold() | `paths = [path for path in references.paths if query.casefold() in path.casefold()]` | |
| `agent/_builder_session.py:850` | `_campaign_content_tools.discover` | .casefold() | `terms = query.casefold().split()` | |
| `agent/_builder_session.py:1578` | `_campaign_source_tool.inspect` | .casefold() | `matches = tuple(item for item in paths if not needle or needle in item.casefold())` | |
| `agent/_content_definition_tool.py:106` | `content_definition_tool.read` | .isdecimal() | `if not token.isdecimal():` | |
| `agent/_native_continuation.py:10` | `requires_reasoning_with_tools` | .casefold() | `identity = model.wire_model_id.casefold().split("/")[-1]` | |
| `agent/_provider_resolver.py:599` | `ConfiguredProviderResolver.model_choices` | .casefold() | `item.provider_name.casefold(),` | |
| `agent/_provider_resolver.py:600` | `ConfiguredProviderResolver.model_choices` | .casefold() | `item.name.casefold(),` | |
| `agent/_provider_session.py:272` | `_contains_serialized_tool_call` | .casefold() | `folded = text.casefold()` | |
| `agent/_provider_session.py:894` | `_normalize_state_references.key` | .casefold() | `return re.sub(r"[\s_-]+", "_", re.sub(r"['’]", "", value.casefold())).strip("_")` | |
| `agent/_provider_session.py:1279` | `_validated_speaker_name` | .isprintable() | `or any(not character.isprintable() for character in value if character != " ")` | |
| `agent/_provider_session.py:1292` | `_speaker_names_overlap` | .casefold() | `first_key, second_key = (" ".join(name.casefold().split()) for name in (first, second))` | |
| `agent/_provider_session.py:2361` | `ProviderSessionAdapter._relevant_character_files_message` | .casefold() | `given_name.casefold() != aliases[0].casefold()` | |
| `agent/_provider_session.py:2362` | `ProviderSessionAdapter._relevant_character_files_message` | .casefold() | `and given_name_counts.get(given_name.casefold()) == 1` | |
| `application/character_chat.py:463` | `persona_prompt` | .capitalize() | `sections.append(_section(key.capitalize(), biography.get(key)))` | |
| `application/character_chat.py:556` | `clean_reply` | .casefold() | `if reply.casefold().startswith(prefix.casefold()):` | |
| `config/provider_settings.py:424` | `_provider_profile_id` | .casefold() | `digest = hashlib.sha256(f"{display_name.casefold()}\n{base_url}".encode()).hexdigest()[:16]` | |
| `config/provider_settings.py:582` | `_private_headers` | .casefold() | `folded_name = raw_name.casefold()` | |
| `config/provider_settings.py:831` | `_discovery_model` | .casefold() | `if preset_id in {"opencode_go", "opencode_zen"} and model_id.casefold().startswith("jev-"):` | |
| `config/provider_settings.py:1370` | `discover_provider_models` | .casefold() | `and search not in model_id.casefold()` | |
| `provider/_model_facts.py:270` | `inferred_model_protocol` | .casefold() | `identity = model_id.casefold()` | |
| `provider/_openai_compat.py:113` | `_InlineReasoningFilter.feed` | .casefold() | `lowered = self._buffer.casefold()` || yes |
| `provider/_openai_compat.py:117` | `_InlineReasoningFilter.feed` | .casefold() | `if (index := lowered.find(marker.casefold())) >= 0` || yes |
| `provider/_openai_compat.py:155` | `_InlineReasoningFilter._partial_marker_suffix` | .casefold() | `if any(marker.casefold().startswith(suffix) for marker, _ in cls._MARKERS):` || yes |
| `provider/_reasoning.py:131` | `configure_reasoning` | .casefold() | `model.transport == "anthropic_messages" and wire_id.casefold() == "minimax-m3"` | |
| `provider/_request_metadata.py:23` | `<module>` | .casefold() | `_OPENCODE_SESSION_HEADER.casefold(),` | |
| `provider/_request_metadata.py:24` | `<module>` | .casefold() | `_OPENCODE_CLIENT_HEADER.casefold(),` | |
| `provider/_request_metadata.py:25` | `<module>` | .casefold() | `_USER_AGENT_HEADER.casefold(),` | |
| `session/_prompt_assembler.py:813` | `PromptAssembler._extract_distance_text` | .capitalize() | `band_descs.append(f"{name.capitalize()} {desc}")` | |
| `session/_prompt_assembler.py:815` | `PromptAssembler._extract_distance_text` | .capitalize() | `band_descs.append(name.capitalize())` | |

## Lane 5: Owner, clients and release

| Where | Function | Kind | Code | Done |
| --- | --- | --- | --- | --- |
| `application/desktop_update/manifest.py:67` | `Version.parse` | .isdigit() | `if any(part.isdigit() and len(part) > 1 and part.startswith("0") for part in pre):` | `update_vectors` |
| `application/desktop_update/manifest.py:92` | `Version.__lt__` | .isdigit() | `if left.isdigit() and right.isdigit():` | `update_vectors` |
| `application/desktop_update/manifest.py:94` | `Version.__lt__` | .isdigit() | `if left.isdigit() != right.isdigit():` | `update_vectors` |
| `application/desktop_update/manifest.py:95` | `Version.__lt__` | .isdigit() | `return left.isdigit()` | `update_vectors` |
| `application/desktop_update/manifest.py:253` | `payload_path` | .casefold() | `or value.casefold() in {"authority.sqlite3", "config.yaml"}` | `update_vectors` |
| `application/desktop_update/manifest.py:419` | `_parse_manifest` | .casefold() | `if name.casefold() in names:` | `update_vectors` |
| `application/desktop_update/manifest.py:421` | `_parse_manifest` | .casefold() | `names.add(name.casefold())` | `update_vectors` |
| `application/desktop_update/manifest.py:422` | `_parse_manifest` | .casefold() | `maximum = 128 if name.casefold() == "sylvia-portable.json" else ARTIFACT_LIMIT` | `update_vectors` |
| `application/desktop_update/manifest.py:440` | `_parse_manifest` | .casefold() | `if not {name.casefold() for name in required} <= names:` | `update_vectors` |
| `application/desktop_update/replacement.py:139` | `_inventory` | .casefold() | `name.casefold() in names` | `preparation_vectors` |
| `application/desktop_update/replacement.py:152` | `_inventory` | .casefold() | `names.add(name.casefold())` | `preparation_vectors` |
| `application/desktop_update/replacement.py:273` | `PortableReplacement._restore_receipt` | .casefold() | `if len({entry.path.casefold() for entry in files}) != len(files):` | |
| `application/desktop_update/transport.py:238` | `content_length` | .isdigit() | `or not length.isdigit()` | `update_vectors` |
| `interfaces/owner_process.py:313` | `_package_version_priority` | .casefold() | `return (0, 0, 0, 0, 0, (), version.casefold())` | |
| `interfaces/owner_process.py:316` | `_package_version_priority` | .casefold() | `(0, f"{int(part):020d}") if part.isdigit() else (1, part.casefold())` | |
| `interfaces/owner_process.py:316` | `_package_version_priority` | .isdigit() | `(0, f"{int(part):020d}") if part.isdigit() else (1, part.casefold())` | |
| `interfaces/owner_process.py:326` | `_package_version_priority` | .casefold() | `version.casefold(),` | |
| `interfaces/owner_process.py:1176` | `_selection_from_reference` | .casefold() | `folded = reference.casefold()` | |
| `interfaces/owner_process.py:1188` | `_selection_from_reference` | .casefold() | `candidates = {model_id.casefold(), upstream_id.casefold()}` | |
| `interfaces/owner_process.py:1190` | `_selection_from_reference` | .casefold() | `candidates.add(model_name.casefold())` | |
| `local_dictation_process.py:122` | `serve_session` | .isalnum() | `c.isascii() and (c.isalnum() or c == "-") for c in identity` | `dictation_vectors` (non-ASCII letters, a space, `-`) |
| `release_artifact.py:336` | `_validated_artifacts` | .casefold() | `if source.suffix.casefold() != expected_suffix:` | |
| `release_artifact.py:338` | `_validated_artifacts` | .casefold() | `folded = name.casefold()` | |
| `release_artifact.py:348` | `_validated_artifacts` | .casefold() | `return tuple(sorted(validated, key=lambda item: item.source.name.casefold()))` | |
| `release_artifact.py:726` | `verify_release_layout` | .casefold() | `if name.casefold() in seen:` | |
| `release_artifact.py:728` | `verify_release_layout` | .casefold() | `seen.add(name.casefold())` | |
| `release_artifact.py:749` | `verify_release_layout` | .casefold() | `if path.suffix.casefold() != platform.artifact_suffix[kind]:` | |
