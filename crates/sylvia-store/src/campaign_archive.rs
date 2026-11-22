//! The frozen portable campaign archive ABI from `campaign_archive.py` that the store's verifiers
//! and the export share: the format identity, the portable table projection and its schema-claim
//! hash (`authority_columns`, `source_authority_schema_claim_sha256`), and the migration
//! provenance a portable archive records (`validate_migration_provenance`, with its expected
//! lineages).
//!
//! The tables were written once from Python's `TABLES_IN_ORDER`, `TABLE_COLUMNS` and
//! `TABLE_KEYS`; `tests/campaign_archive.rs` checks them, every schema claim and the lineages
//! against Python's values.

use crate::error::{StoreError, StoreResult};
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::pyops::py_eq;

/// `FORMAT_ID`.
pub const FORMAT_ID: &str = "sylvia-gm-campaign-export";
/// `FORMAT_VERSION`.
pub const FORMAT_VERSION: i64 = 1;
/// `PRODUCT_ID`.
pub const PRODUCT_ID: &str = "sylvia-gm";
/// `PRODUCT_GENERATION`.
pub const PRODUCT_GENERATION: &str = "alpha";
/// `PREDECESSOR_SOURCE_AUTHORITY_SCHEMA_VERSION`.
pub const PREDECESSOR_SOURCE_AUTHORITY_SCHEMA_VERSION: i64 = 9;
/// `PRE_REASONING_SOURCE_AUTHORITY_SCHEMA_VERSION`.
pub const PRE_REASONING_SOURCE_AUTHORITY_SCHEMA_VERSION: i64 = 10;
/// `PRE_EFFORT_SOURCE_AUTHORITY_SCHEMA_VERSION`.
pub const PRE_EFFORT_SOURCE_AUTHORITY_SCHEMA_VERSION: i64 = 11;
/// `SOURCE_AUTHORITY_SCHEMA_VERSION`.
pub const SOURCE_AUTHORITY_SCHEMA_VERSION: i64 = 12;
/// Every source schema an archive may claim.
pub const SOURCE_AUTHORITY_SCHEMA_VERSIONS: [i64; 4] = [
    PREDECESSOR_SOURCE_AUTHORITY_SCHEMA_VERSION,
    PRE_REASONING_SOURCE_AUTHORITY_SCHEMA_VERSION,
    PRE_EFFORT_SOURCE_AUTHORITY_SCHEMA_VERSION,
    SOURCE_AUTHORITY_SCHEMA_VERSION,
];

/// One portable table: its name, its exported columns and its key.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ArchiveTable {
    pub name: &'static str,
    pub columns: &'static [&'static str],
    pub keys: &'static [&'static str],
}

/// `TABLES_IN_ORDER`, each with its `TABLE_COLUMNS` and `TABLE_KEYS` entries.
pub const TABLES: &[ArchiveTable] = &[
    ArchiveTable {
        name: "campaigns",
        columns: &[
            "campaign_id",
            "display_name",
            "lifecycle",
            "writer",
            "revision",
            "lock_hash",
            "current_lock_id",
        ],
        keys: &["campaign_id"],
    },
    ArchiveTable {
        name: "campaign_lifecycle_state",
        columns: &[
            "campaign_id",
            "retained_active_writer",
            "designated_session_id",
            "tombstone_prior_lifecycle",
            "tombstone_prior_writer",
            "tombstone_prior_session_id",
        ],
        keys: &["campaign_id"],
    },
    ArchiveTable {
        name: "campaign_lifecycle_transitions",
        columns: &[
            "operation_id",
            "campaign_id",
            "operation_kind",
            "basis_revision",
            "resulting_revision",
            "from_lifecycle",
            "to_lifecycle",
            "from_writer",
            "to_writer",
            "designated_session_id",
        ],
        keys: &["operation_id"],
    },
    ArchiveTable {
        name: "campaign_operations",
        columns: &[
            "operation_id",
            "operation_kind",
            "campaign_id",
            "semantic_payload_json",
            "semantic_payload_sha256",
            "result_json",
            "basis_turn_id",
            "basis_lock_id",
            "basis_revision",
            "resulting_revision",
        ],
        keys: &["operation_id"],
    },
    ArchiveTable {
        name: "campaign_constructions",
        columns: &[
            "campaign_id",
            "confirmation_operation_id",
            "authority_turn_id",
            "authority_lock_id",
            "confirmation_revision",
            "lane",
            "canonical_input_json",
            "begin_operation_id",
            "begin_revision",
            "canonical_plan_json",
            "completion_operation_id",
            "completion_revision",
            "canonical_representation_sha256",
        ],
        keys: &["campaign_id"],
    },
    ArchiveTable {
        name: "campaign_continuity_records",
        columns: &[
            "campaign_id",
            "sequence",
            "entry_id",
            "record_id",
            "kind",
            "status",
            "payload_json",
            "basis_message_ids_json",
            "operation_id",
            "admitted_revision",
        ],
        keys: &["campaign_id", "sequence"],
    },
    ArchiveTable {
        name: "campaign_revisions",
        columns: &[
            "campaign_id",
            "revision",
            "action_id",
            "activation_id",
            "plan_id",
            "handoff_id",
            "construction_operation_id",
            "lifecycle_operation_id",
            "continuity_operation_id",
        ],
        keys: &["campaign_id", "revision"],
    },
    ArchiveTable {
        name: "campaign_package_memberships",
        columns: &["campaign_id", "package_id", "version", "sha256", "role", "load_order"],
        keys: &["campaign_id", "load_order"],
    },
    ArchiveTable {
        name: "participants",
        columns: &[
            "campaign_id",
            "participant_id",
            "kind",
            "package_id",
            "package_version",
            "data_json",
            "package_sha256",
            "schema_id",
            "schema_sha256",
            "validation_status",
            "adaptation_id",
        ],
        keys: &["campaign_id", "participant_id"],
    },
    ArchiveTable {
        name: "participant_quarantines",
        columns: &["campaign_id", "participant_id", "reason_code"],
        keys: &["campaign_id", "participant_id"],
    },
    ArchiveTable {
        name: "campaign_state_namespace_owners",
        columns: &["campaign_id", "namespace", "owner_package_id"],
        keys: &["campaign_id", "namespace"],
    },
    ArchiveTable {
        name: "campaign_state",
        columns: &["campaign_id", "namespace", "state_key", "value_json"],
        keys: &["campaign_id", "namespace", "state_key"],
    },
    ArchiveTable {
        name: "turns",
        columns: &["campaign_id", "turn_id", "status", "created_revision"],
        keys: &["campaign_id", "turn_id"],
    },
    ArchiveTable {
        name: "actions",
        columns: &[
            "campaign_id",
            "action_id",
            "turn_id",
            "expected_revision",
            "resulting_revision",
            "writer",
            "package_id",
            "package_version",
            "package_sha256",
            "tool_name",
            "status",
            "payload_json",
            "payload_hash",
            "result_json",
            "lock_id",
        ],
        keys: &["campaign_id", "action_id"],
    },
    ArchiveTable {
        name: "idempotency_records",
        columns: &[
            "campaign_id",
            "idempotency_key",
            "payload_hash",
            "action_id",
            "status",
            "result_json",
            "resulting_revision",
        ],
        keys: &["campaign_id", "idempotency_key"],
    },
    ArchiveTable {
        name: "canonical_events",
        columns: &[
            "campaign_id",
            "sequence",
            "event_type",
            "turn_id",
            "action_id",
            "safe_payload_json",
            "visibility",
            "attempt_id",
            "attempt_ordinal",
        ],
        keys: &["campaign_id", "sequence"],
    },
    ArchiveTable {
        name: "narration_interruptions",
        columns: &[
            "campaign_id",
            "interruption_id",
            "turn_id",
            "last_action_id",
            "failed_attempt_id",
            "status",
            "resolving_retry_id",
            "resolved_sequence",
        ],
        keys: &["campaign_id", "interruption_id"],
    },
    ArchiveTable {
        name: "mutations",
        columns: &[
            "campaign_id",
            "action_id",
            "ordinal",
            "namespace",
            "state_key",
            "before_json",
            "after_json",
        ],
        keys: &["campaign_id", "action_id", "ordinal"],
    },
    ArchiveTable {
        name: "consequences",
        columns: &["campaign_id", "action_id", "ordinal", "payload_json"],
        keys: &["campaign_id", "action_id", "ordinal"],
    },
    ArchiveTable {
        name: "campaign_locks",
        columns: &[
            "lock_id",
            "campaign_id",
            "closure_sha256",
            "availability",
            "predecessor_lock_id",
            "created_revision",
        ],
        keys: &["lock_id"],
    },
    ArchiveTable {
        name: "campaign_lock_memberships",
        columns: &["lock_id", "closure_order", "package_id", "version", "content_sha256", "role"],
        keys: &["lock_id", "closure_order"],
    },
    ArchiveTable {
        name: "package_activations",
        columns: &[
            "activation_id",
            "campaign_id",
            "payload_sha256",
            "candidate_id",
            "old_lock_id",
            "new_lock_id",
            "migration_id",
            "status",
            "resulting_revision",
            "result_json",
        ],
        keys: &["activation_id"],
    },
    ArchiveTable {
        name: "campaign_plans",
        columns: &[
            "plan_id",
            "campaign_id",
            "package_lock_id",
            "source_session_id",
            "content_sha256",
            "content_json",
            "provenance_json",
            "validation_outcome",
            "acceptance_status",
            "plan_revision",
            "resulting_campaign_revision",
            "payload_sha256",
        ],
        keys: &["plan_id"],
    },
    ArchiveTable {
        name: "builder_dm_handoffs",
        columns: &[
            "handoff_id",
            "campaign_id",
            "payload_sha256",
            "builder_session_id",
            "dm_session_id",
            "package_lock_id",
            "package_candidate_id",
            "plan_id",
            "plan_revision",
            "plan_content_sha256",
            "expected_revision",
            "resulting_campaign_revision",
            "chronology_ordinal",
            "completed_turn_id",
            "safe_payload_json",
        ],
        keys: &["handoff_id"],
    },
    ArchiveTable {
        name: "agent_sessions",
        columns: &[
            "session_id",
            "campaign_id",
            "role",
            "provider_id",
            "model_id",
            "status",
            "parent_session_id",
            "selected_skill",
            "scope_json",
            "payload_sha256",
            "reasoning_effort",
            "reasoning_effort_once",
        ],
        keys: &["session_id"],
    },
    ArchiveTable {
        name: "pending_mechanics_actions",
        columns: &[
            "campaign_id",
            "action_id",
            "turn_id",
            "basis_revision",
            "writer",
            "package_id",
            "package_version",
            "package_sha256",
            "lock_id",
            "tool_name",
            "parameters_json",
            "payload_hash",
            "initiating_session_id",
            "choice_id",
            "descriptor_json",
            "descriptor_sha256",
        ],
        keys: &["campaign_id", "action_id"],
    },
    ArchiveTable {
        name: "pending_mechanics_action_aliases",
        columns: &["campaign_id", "idempotency_key", "action_id", "payload_hash"],
        keys: &["campaign_id", "idempotency_key"],
    },
    ArchiveTable {
        name: "mechanics_choice_resolutions",
        columns: &[
            "campaign_id",
            "resolution_id",
            "action_id",
            "choice_id",
            "option_id",
            "resolution_sha256",
            "descriptor_sha256",
            "session_id",
            "resulting_revision",
        ],
        keys: &["campaign_id", "resolution_id"],
    },
    ArchiveTable {
        name: "agent_session_messages",
        columns: &["session_id", "sequence", "message_id", "kind", "content_json", "canonical_sequence"],
        keys: &["session_id", "sequence"],
    },
    ArchiveTable {
        name: "package_candidates",
        columns: &["candidate_id", "install_id", "status", "closure_sha256", "report_json"],
        keys: &["candidate_id"],
    },
    ArchiveTable {
        name: "package_candidate_memberships",
        columns: &["candidate_id", "closure_order", "package_id", "version", "content_sha256"],
        keys: &["candidate_id", "closure_order"],
    },
    ArchiveTable {
        name: "package_staging",
        columns: &["install_id", "status", "manifest_json", "report_json"],
        keys: &["install_id"],
    },
    ArchiveTable {
        name: "packages",
        columns: &["package_id", "version", "sha256", "capabilities_json"],
        keys: &["package_id", "version", "sha256"],
    },
    ArchiveTable {
        name: "package_dependencies",
        columns: &[
            "package_id",
            "version",
            "sha256",
            "dependency_order",
            "dependency_id",
            "dependency_version",
            "dependency_sha256",
            "dependency_kind",
            "dependency_namespace",
        ],
        keys: &["package_id", "version", "sha256", "dependency_order"],
    },
    ArchiveTable {
        name: "package_manifests",
        columns: &[
            "package_id",
            "version",
            "content_sha256",
            "role",
            "canonical_json",
            "metadata_json",
            "provenance_json",
            "compatibility_json",
            "status",
        ],
        keys: &["package_id", "version", "content_sha256"],
    },
    ArchiveTable {
        name: "package_manifest_files",
        columns: &[
            "package_id",
            "version",
            "content_sha256",
            "file_order",
            "portable_path",
            "byte_count",
            "blob_sha256",
        ],
        keys: &["package_id", "version", "content_sha256", "file_order"],
    },
    ArchiveTable {
        name: "package_exact_dependencies",
        columns: &[
            "package_id",
            "version",
            "content_sha256",
            "dependency_order",
            "dependency_namespace",
            "dependency_kind",
            "dependency_id",
            "dependency_version",
            "dependency_content_sha256",
        ],
        keys: &["package_id", "version", "content_sha256", "dependency_order"],
    },
    ArchiveTable {
        name: "package_capabilities",
        columns: &["package_id", "version", "content_sha256", "capability_order", "capability"],
        keys: &["package_id", "version", "content_sha256", "capability_order"],
    },
    ArchiveTable {
        name: "package_tools",
        columns: &[
            "package_id",
            "version",
            "content_sha256",
            "tool_order",
            "tool_name",
            "capability",
            "input_schema_json",
            "state_predicates_json",
            "turn_statuses_json",
            "writers_json",
        ],
        keys: &["package_id", "version", "content_sha256", "tool_order"],
    },
    ArchiveTable {
        name: "package_prompt_fragments",
        columns: &["package_id", "version", "content_sha256", "surface_order", "fragment_id", "capability"],
        keys: &["package_id", "version", "content_sha256", "surface_order"],
    },
    ArchiveTable {
        name: "package_ui_surfaces",
        columns: &["package_id", "version", "content_sha256", "surface_order", "surface_id", "capability"],
        keys: &["package_id", "version", "content_sha256", "surface_order"],
    },
    ArchiveTable {
        name: "package_participant_artifacts",
        columns: &[
            "package_id",
            "version",
            "content_sha256",
            "artifact_order",
            "kind",
            "schema_id",
            "schema_sha256",
            "schema_json",
        ],
        keys: &["package_id", "version", "content_sha256", "artifact_order"],
    },
    ArchiveTable {
        name: "package_blobs",
        columns: &["blob_sha256", "byte_count", "chunk_count"],
        keys: &["blob_sha256"],
    },
];

/// `MIGRATION_PROVENANCE_FIELDS`.
pub const MIGRATION_PROVENANCE_FIELDS: [&str; 2] = ["schema_migrations", "schema_extensions"];
/// `SCHEMA_MIGRATION_FIELDS`.
pub const SCHEMA_MIGRATION_FIELDS: [&str; 3] = ["version", "applied_name", "sql_sha256"];
/// `SCHEMA_EXTENSION_FIELDS`.
pub const SCHEMA_EXTENSION_FIELDS: [&str; 4] =
    ["extension_name", "extension_sha256", "predecessor_migration_name", "predecessor_sql_sha256"];

/// `EXPECTED_SCHEMA_MIGRATION_PROVENANCE`: migrations 1 to 12 as a schema-12 archive records them.
/// The predecessor (schema 9) lineage is the first nine, the pre-reasoning (10) the first ten and
/// the pre-effort (11) the first eleven.
pub const EXPECTED_SCHEMA_MIGRATION_PROVENANCE: [(i64, &str, &str); 12] = [
    (1, "f1_neutral_authority", "6c416e59e5d38a3018845a8cb320b24e3bff74a169377735db048c01d4bff8d1"),
    (2, "f2_provisional_event_protocol", "71e1c58545c1f8ce9f8bca24810987648a35c4779bbcdeecff947c8181b1b193"),
    (3, "f3_sqlite_package_library", "58e3af1675b8232a8ef83211f8fe49161ec464a04bbae065db436a909d70ea3f"),
    (4, "f4_builder_dm_sessions", "e0f2766f3f18d160493f4008ac920b79d0079c6475e6f59a970f05615b73e9fb"),
    (5, "f5_campaign_lifecycle_recovery", "ed0c1f6770b9ffe2b1b2c0f3c1b1b4edf1666b0af23f337f46742ec103fd2483"),
    (
        6,
        "f7_relational_state_namespace_ownership",
        "91c930b8b45fde6f03936318c4c0866dc46f9a88c3321d2fd4cef8800643eae6",
    ),
    (7, "mechanics_choice_reservations", "5a15dae72c9a64f4ea22cd80b81ab15a3c71c23ec4e9ed57503da30eff873d53"),
    (8, "standalone_builder_chats", "70c67e3d1c0b8c9371f8a67f924eea3975aa23dab4e47f07488541983c7538d0"),
    (9, "campaign_state_continuity", "6b4c74ea0d67ea324b496f076a16c8976b3b8921c8ec7eed2aa6056497308bee"),
    (10, "unbound_draft_lifecycle", "3c5c44a28eea815f08e4cc126bad75b551f00602dc1965b891aa5cc06c14ce1f"),
    (11, "private_provider_records", "4fe61f98e20ef40b346e3304942b216fdeb52a5decb383607aec6a44e397cf34"),
    (12, "session_reasoning_preferences", "d1e89bcbf082d1046e7eec8326e9bc2ba3217b1c2439bcc0667c8201ebb6ca28"),
];

/// `EXPECTED_SCHEMA_EXTENSION_PROVENANCE`, sorted.
pub const EXPECTED_SCHEMA_EXTENSION_PROVENANCE: [(&str, &str, &str, &str); 3] = [
    (
        "f5_portable_campaign_recovery_v1",
        "d871df794682ae39c1d10cecf3378e471c02108ae9784782d4ac3c4693d77b61",
        "f5_reversible_lifecycle_v3",
        "a05b1892abf39629612602f2f07be62048e7a0d6ecd46e337039e89b30266a06",
    ),
    (
        "f5_probe_01_stable_campaign_identity",
        "0c252567c25ccbb3f2b81eb4a3f54e4fdc4e009220eac0e6d61f2f045cb1a7b4",
        "f5_campaign_lifecycle_recovery",
        "49d01f74743bd178ab23848c4493dda234bf51a26ad7115236c2356714c48802",
    ),
    (
        "f5_reversible_lifecycle_v3",
        "a05b1892abf39629612602f2f07be62048e7a0d6ecd46e337039e89b30266a06",
        "f5_probe_01_stable_campaign_identity",
        "0c252567c25ccbb3f2b81eb4a3f54e4fdc4e009220eac0e6d61f2f045cb1a7b4",
    ),
];

/// `PURGE_SCHEMA_EXTENSION_PROVENANCE`.
pub const PURGE_SCHEMA_EXTENSION_PROVENANCE: (&str, &str, &str, &str) = (
    "f5_campaign_purge_v1",
    "1de84ac0fe9870ee106630aad69f12281322121cbc549f5f396192f0a384cd3b",
    "f5_portable_campaign_recovery_v1",
    "d871df794682ae39c1d10cecf3378e471c02108ae9784782d4ac3c4693d77b61",
);

/// The migration lineage an archive from `source_schema_version` records.
pub fn expected_migrations(source_schema_version: i64) -> &'static [(i64, &'static str, &'static str)] {
    let count = match source_schema_version {
        PREDECESSOR_SOURCE_AUTHORITY_SCHEMA_VERSION => 9,
        PRE_REASONING_SOURCE_AUTHORITY_SCHEMA_VERSION => 10,
        PRE_EFFORT_SOURCE_AUTHORITY_SCHEMA_VERSION => 11,
        _ => 12,
    };
    &EXPECTED_SCHEMA_MIGRATION_PROVENANCE[..count]
}

/// `_failure(code)`: `CommandRejected("Portable campaign export is invalid", code=code)`.
pub fn failure(code: &str) -> StoreError {
    crate::actions::rejected("Portable campaign export is invalid", code)
}

/// `_json_bytes(value)`: insertion order, compact, not ASCII-escaped.
pub fn json_bytes(value: &Json) -> Vec<u8> {
    pyjson::dump_bytes(value, Dump::CANONICAL.sorted(false))
}

/// One table by name.
pub fn table(name: &str) -> Option<&'static ArchiveTable> {
    TABLES.iter().find(|table| table.name == name)
}

/// `authority_columns(table, source_schema_version)`: archives made before effort preferences
/// export `agent_sessions` without its last two columns.
pub fn authority_columns(table: &ArchiveTable, source_schema_version: i64) -> &'static [&'static str] {
    if table.name == "agent_sessions" && source_schema_version <= PRE_EFFORT_SOURCE_AUTHORITY_SCHEMA_VERSION {
        &table.columns[..table.columns.len() - 2]
    } else {
        table.columns
    }
}

/// `source_authority_schema_claim_sha256(source_schema_version)`: the hash of the frozen
/// portable projection.
pub fn source_authority_schema_claim_sha256(source_schema_version: i64) -> String {
    let tables: Vec<Json> = TABLES
        .iter()
        .map(|table| {
            let mut entry = JsonMap::new();
            entry.insert("name".into(), table.name.into());
            entry.insert(
                "columns".into(),
                Json::Array(
                    authority_columns(table, source_schema_version)
                        .iter()
                        .map(|&column| column.into())
                        .collect(),
                ),
            );
            entry.insert("key".into(), Json::Array(table.keys.iter().map(|&key| key.into()).collect()));
            Json::Object(entry)
        })
        .collect();
    let mut preimage = JsonMap::new();
    preimage.insert("source_authority_schema_version".into(), source_schema_version.into());
    preimage.insert("tables".into(), Json::Array(tables));
    sylvia_base::hash::sha256_hex(json_bytes(&Json::Object(preimage)))
}

/// `re.fullmatch(r"[a-z0-9][a-z0-9_]{0,127}", value)`.
fn valid_provenance_name(value: &Json) -> bool {
    let Json::String(text) = value else { return false };
    let bytes = text.as_bytes();
    !bytes.is_empty()
        && bytes.len() <= 128
        && (bytes[0].is_ascii_lowercase() || bytes[0].is_ascii_digit())
        && bytes.iter().all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || *byte == b'_')
}

/// `re.fullmatch(r"[0-9a-f]{64}", value)`.
fn valid_sha256(value: &Json) -> bool {
    matches!(value, Json::String(text)
        if text.len() == 64 && text.bytes().all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)))
}

/// Whether `object`'s keys are exactly `fields` (as a set), and when `ordered`, in that order.
fn has_fields(object: &JsonMap, fields: &[&str], ordered: bool) -> bool {
    object.len() == fields.len()
        && fields.iter().all(|field| object.contains_key(*field))
        && (!ordered || object.keys().zip(fields).all(|(key, field)| key == field))
}

/// `int(value)` of a value already compared equal to an integer (`True` and `1.0` among them).
fn as_int(value: &Json) -> i64 {
    match value {
        Json::Bool(flag) => i64::from(*flag),
        Json::Number(number) => number.as_i64().unwrap_or_else(|| number.as_f64().unwrap_or(-1.0) as i64),
        _ => -1,
    }
}

/// `extension_names != sorted(extension_names) or len(extension_names) != len(set(...))`.
/// Python orders strings with strings and numbers with numbers; anything else is its
/// `TypeError`, a fault rather than a refusal.
fn names_sorted_and_unique(names: &[&Json]) -> StoreResult<bool> {
    if names.len() < 2 {
        return Ok(true);
    }
    let distinct = (0..names.len()).all(|i| (i + 1..names.len()).all(|j| !py_eq(names[i], names[j])));
    if names.iter().all(|name| matches!(name, Json::String(_))) {
        let texts: Vec<&str> = names.iter().filter_map(|name| name.as_str()).collect();
        return Ok(texts.windows(2).all(|pair| pair[0] <= pair[1]) && distinct);
    }
    if names.iter().all(|name| matches!(name, Json::Number(_) | Json::Bool(_))) {
        let values: Vec<f64> = names
            .iter()
            .map(|name| match name {
                Json::Bool(flag) => f64::from(u8::from(*flag)),
                other => other.as_f64().unwrap_or(f64::NAN),
            })
            .collect();
        return Ok(values.windows(2).all(|pair| pair[0] <= pair[1]) && distinct);
    }
    Err(StoreError::Other("'<' not supported between these extension names".into()))
}

/// `validate_migration_provenance(value, enforce_declared_order_bytes=...,
/// source_schema_version=...)`.
pub fn validate_migration_provenance(
    value: &Json,
    enforce_declared_order_bytes: bool,
    source_schema_version: &Json,
) -> StoreResult<()> {
    let invalid = || failure("portable_export_invalid_migration_provenance");
    let Json::Object(provenance) = value else { return Err(invalid()) };
    if !has_fields(provenance, &MIGRATION_PROVENANCE_FIELDS, false) {
        return Err(invalid());
    }
    if enforce_declared_order_bytes && !has_fields(provenance, &MIGRATION_PROVENANCE_FIELDS, true) {
        return Err(failure("portable_export_invalid_manifest_canonical_bytes"));
    }
    let Some(Json::Array(migrations)) = provenance.get("schema_migrations") else { return Err(invalid()) };
    if migrations.is_empty() {
        return Err(invalid());
    }
    let Some(Json::Array(extensions)) = provenance.get("schema_extensions") else { return Err(invalid()) };
    let objects = |items: &[Json], fields: &[&str]| -> Option<Vec<JsonMap>> {
        items
            .iter()
            .map(|item| match item {
                Json::Object(object) if has_fields(object, fields, enforce_declared_order_bytes) => {
                    Some(object.clone())
                }
                _ => None,
            })
            .collect()
    };
    let Some(migrations) = objects(migrations, &SCHEMA_MIGRATION_FIELDS) else { return Err(invalid()) };
    let Some(extensions) = objects(extensions, &SCHEMA_EXTENSION_FIELDS) else { return Err(invalid()) };
    // `[item["version"] for item in migrations] != list(range(1, len(migrations) + 1))`.
    if !migrations
        .iter()
        .enumerate()
        .all(|(index, item)| py_eq(&item["version"], &Json::from(index as i64 + 1)))
    {
        return Err(invalid());
    }
    if migrations
        .iter()
        .any(|item| !valid_provenance_name(&item["applied_name"]) || !valid_sha256(&item["sql_sha256"]))
    {
        return Err(invalid());
    }
    let names: Vec<&Json> = extensions.iter().map(|item| &item["extension_name"]).collect();
    if !names_sorted_and_unique(&names)? {
        return Err(invalid());
    }
    if extensions.iter().any(|item| {
        !valid_provenance_name(&item["extension_name"])
            || !valid_sha256(&item["extension_sha256"])
            || !valid_provenance_name(&item["predecessor_migration_name"])
            || !valid_sha256(&item["predecessor_sql_sha256"])
    }) {
        return Err(invalid());
    }
    let text = |item: &JsonMap, key: &str| item[key].as_str().unwrap_or_default().to_owned();
    let migration_rows: Vec<(i64, String, String)> = migrations
        .iter()
        .map(|item| (as_int(&item["version"]), text(item, "applied_name"), text(item, "sql_sha256")))
        .collect();
    let extension_rows: Vec<(String, String, String, String)> = extensions
        .iter()
        .map(|item| {
            (
                text(item, "extension_name"),
                text(item, "extension_sha256"),
                text(item, "predecessor_migration_name"),
                text(item, "predecessor_sql_sha256"),
            )
        })
        .collect();
    // `type(source_schema_version) is not int or source_schema_version not in {...}`.
    let version = match source_schema_version {
        Json::Number(number) => number.as_i64(),
        _ => None,
    };
    let Some(version) = version.filter(|version| SOURCE_AUTHORITY_SCHEMA_VERSIONS.contains(version)) else {
        return Err(failure("portable_export_invalid_source_schema"));
    };
    let owned = |rows: &[(&str, &str, &str, &str)]| -> Vec<(String, String, String, String)> {
        rows.iter().map(|row| (row.0.into(), row.1.into(), row.2.into(), row.3.into())).collect()
    };
    let expected_extensions = owned(&EXPECTED_SCHEMA_EXTENSION_PROVENANCE);
    let mut with_purge = expected_extensions.clone();
    with_purge.extend(owned(&[PURGE_SCHEMA_EXTENSION_PROVENANCE]));
    with_purge.sort();
    let expected: Vec<(i64, String, String)> =
        expected_migrations(version).iter().map(|row| (row.0, row.1.into(), row.2.into())).collect();
    if migration_rows != expected || (extension_rows != expected_extensions && extension_rows != with_purge) {
        return Err(invalid());
    }
    Ok(())
}
