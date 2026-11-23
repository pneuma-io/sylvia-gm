//! `application/reusable_participants.py`: a small owner-local library for exact reusable
//! Player Characters and Companions, and the Setting Character Files they are parsed from.
//!
//! `ParticipantValidationError` is [`DomainError::Issues`]: a `ValueError` carrying the
//! schema's field-level issues. A Character File holding `NaN` or `Infinity` (which Python's
//! `json.loads` reads) is refused as invalid JSON: differences row 2.

use crate::character_profiles::validate_source_profile;
use crate::initial_character_state::{materialize_initial_character_state, verify_initial_character_state};
use crate::reusable_library_deletion::{
    delete_exact_library_entry, validate_owned_files, validated_library_entry,
};
use crate::reusable_plans::{
    atomic_write, catalog_catches, loads, lower_hex, read_bytes, read_text, temporary_name, unicode_error,
};
use crate::setting_character_requirements::validate_setting_character_data;
use crate::srd_lite_character_compatibility::retained_equipment_character_contract;
use crate::srd_lite_weapon_set_migration::{migrate_state, MIGRATION_ID, PACKAGE_ID};
use crate::workspace_files::os_error;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson::{loads_hooked, HookPolicy};
use sylvia_base::{pyjson, pystr, Json, JsonMap};
use sylvia_domain::authority::PackageLockEntry;
use sylvia_domain::campaign_plan::PackageBinding;
use sylvia_domain::content::validate_content_shape;
use sylvia_domain::error::{DomainError, DomainResult, RulesetDiagnostic};
use sylvia_domain::package_library::{canonical_json_bytes, ParticipantArtifactDeclaration};
use sylvia_domain::pyops::{py_eq, py_str};
use sylvia_domain::schema_admission::{validate_json_schema, validate_parameters};

pub const REUSABLE_PARTICIPANT_FORMAT_ID: &str = "sylvia-gm-reusable-participant";
pub const REUSABLE_PARTICIPANT_FORMAT_VERSION: i64 = 5;
pub const SETTING_CHARACTER_FORMAT_ID: &str = "sylvia-gm-character-file";
pub const SETTING_CHARACTER_FORMAT_VERSION: i64 = 2;
const MAX_LIBRARY_PARTICIPANTS: usize = 256;
const MAX_PARTICIPANT_BYTES: usize = 256 * 1024;
const LIBRARY_NAME: &str = "participants";
const OWNED_NAMES: [&str; 3] = ["profile.md", "payload.json", "record.json"];
const SCHEMA_MISMATCH: &str = "Reusable participant does not match its exact schema";
const INCOMPATIBLE: &str = "Reusable participant record is incompatible";

/// `_LIBRARY_WRITE_LOCK`.
static LIBRARY_WRITE_LOCK: Mutex<()> = Mutex::new(());

/// `on_invalid(name, error)`: told of each library entry that fails to load.
pub type InvalidHandler<'a> = &'a mut dyn FnMut(&str, &DomainError);

/// `ReusableParticipant`: one validated local participant tied to an exact Setting and Ruleset
/// schema.
#[derive(Debug, Clone, PartialEq)]
pub struct ReusableParticipant {
    pub artifact_id: String,
    pub role: String,
    pub name: String,
    pub summary: String,
    pub authored_profile: Option<String>,
    pub content_sha256: String,
    pub package_id: String,
    pub package_version: String,
    pub package_sha256: String,
    pub schema_id: String,
    pub schema_sha256: String,
    pub campaign_setting_id: String,
    pub campaign_setting_version: String,
    pub campaign_setting_sha256: String,
    pub participant: JsonMap,
    pub entity_state: JsonMap,
    pub setting_data: JsonMap,
    pub source_profile: Option<JsonMap>,
    pub source_migration: Option<JsonMap>,
    pub builtin_starter: bool,
}

fn object(value: Json) -> JsonMap {
    match value {
        Json::Object(map) => map,
        _ => JsonMap::new(),
    }
}

/// `json.loads(canonical_json_bytes(value))`.
fn canonical(value: &Json) -> Json {
    let text = String::from_utf8(canonical_json_bytes(value)).expect("canonical JSON is UTF-8");
    pyjson::loads(&text).expect("canonical JSON reads back")
}

fn binding(id: &str, version: &str, sha256: &str) -> Json {
    let mut map = JsonMap::new();
    map.insert("id".into(), Json::from(id));
    map.insert("version".into(), Json::from(version));
    map.insert("content_sha256".into(), Json::from(sha256));
    Json::Object(map)
}

impl ReusableParticipant {
    /// `profile_status`: a real authored profile, or an older sparse artifact.
    pub fn profile_status(&self) -> &'static str {
        if self.authored_profile.is_some() {
            "authored"
        } else {
            "sparse"
        }
    }

    /// `catalog_payload()`: only the metadata needed to render a safe local choice.
    pub fn catalog_payload(&self) -> JsonMap {
        let mut payload = JsonMap::new();
        payload.insert("kind".into(), Json::from(self.role.as_str()));
        payload.insert("id".into(), Json::from(self.artifact_id.as_str()));
        payload.insert("name".into(), Json::from(self.name.as_str()));
        payload.insert("title".into(), Json::from(self.name.as_str()));
        payload.insert("summary".into(), Json::from(self.summary.as_str()));
        payload.insert("profile_status".into(), Json::from(self.profile_status()));
        payload.insert("campaign_ready".into(), Json::Bool(self.profile_status() == "authored"));
        payload.insert("content_sha256".into(), Json::from(self.content_sha256.as_str()));
        payload.insert("reusable".into(), Json::Bool(true));
        if self.builtin_starter {
            payload.insert("builtin_starter".into(), Json::Bool(true));
        }
        payload.insert("source_profile".into(), self.source_profile.clone().map_or(Json::Null, Json::Object));
        payload
            .insert("ruleset".into(), binding(&self.package_id, &self.package_version, &self.package_sha256));
        let mut schema = JsonMap::new();
        schema.insert("id".into(), Json::from(self.schema_id.as_str()));
        schema.insert("content_sha256".into(), Json::from(self.schema_sha256.as_str()));
        payload.insert("schema".into(), Json::Object(schema));
        payload.insert(
            "campaign_setting".into(),
            binding(&self.campaign_setting_id, &self.campaign_setting_version, &self.campaign_setting_sha256),
        );
        payload
    }

    /// `rift_source()`: the private materialization payload for the campaign owner only.
    pub fn rift_source(&self) -> JsonMap {
        let mut source = JsonMap::new();
        source.insert("kind".into(), Json::from(self.role.as_str()));
        source.insert("id".into(), Json::from(self.artifact_id.as_str()));
        source.insert("label".into(), Json::from(self.name.as_str()));
        source.insert("name".into(), Json::from(self.name.as_str()));
        source.insert("summary".into(), Json::from(self.summary.as_str()));
        source.insert("content_sha256".into(), Json::from(self.content_sha256.as_str()));
        source.insert("reusable".into(), Json::Bool(true));
        source
            .insert("authored_profile".into(), self.authored_profile.clone().map_or(Json::Null, Json::from));
        source.insert("profile_status".into(), Json::from(self.profile_status()));
        source.insert("participant".into(), canonical(&Json::Object(self.participant.clone())));
        source.insert("entity_state".into(), canonical(&Json::Object(self.entity_state.clone())));
        source.insert("setting_data".into(), canonical(&Json::Object(self.setting_data.clone())));
        source
    }
}

fn library_root(storage_root: &Path) -> PathBuf {
    storage_root.join(LIBRARY_NAME)
}

/// `_ARTIFACT_ID.fullmatch(artifact_id)`: `participant-[0-9a-f]{24}-[0-9a-f]{16}`.
fn is_artifact_id(artifact_id: &str) -> bool {
    artifact_id
        .strip_prefix("participant-")
        .and_then(|rest| rest.split_once('-'))
        .is_some_and(|(operation, content)| lower_hex(operation, 24) && lower_hex(content, 16))
}

fn invalid(message: &str) -> DomainError {
    DomainError::value(message)
}

/// `_validate_role(role)`.
fn validate_role(role: &str) -> DomainResult<()> {
    if role != "player_character" && role != "companion" {
        return Err(invalid("Reusable participant role is invalid"));
    }
    Ok(())
}

/// The issues of a `MechanicsRejected`, as `ParticipantValidationError` keeps them.
fn participant_validation_error(error: &DomainError) -> DomainError {
    let issues = error.mechanics().map(|details| details.validation_issues.clone()).unwrap_or_default();
    DomainError::Issues { error_type: "ParticipantValidationError", message: SCHEMA_MISMATCH.into(), issues }
}

/// `_validated_payload(...)`: the participant against its exact schema, then the entity state
/// materialized (`creating`) or verified against the Ruleset's declaration.
fn validated_payload(
    role: &str,
    participant: &Json,
    entity_state: &Json,
    declaration: &ParticipantArtifactDeclaration,
    creating: bool,
    setting_data: Option<&JsonMap>,
) -> DomainResult<(JsonMap, JsonMap)> {
    validate_role(role)?;
    let (Json::Object(mut participant_value), Json::Object(mut state_value)) =
        (canonical(participant), canonical(entity_state))
    else {
        return Err(invalid("Reusable participant content must use JSON objects"));
    };
    if declaration
        .schema
        .get("properties")
        .and_then(Json::as_object)
        .is_some_and(|p| p.contains_key("party_role"))
    {
        participant_value.insert("party_role".into(), Json::from(role));
    }
    let checked = validate_json_schema(&declaration.schema, "Reusable participant schema")
        .and_then(|()| validate_parameters(&participant_value, &declaration.schema));
    if let Err(error) = checked {
        if error.python_class() == "MechanicsRejected" {
            return Err(participant_validation_error(&error));
        }
        return Err(invalid(SCHEMA_MISMATCH));
    }
    let participant_json = Json::Object(participant_value.clone());
    let setting = setting_data.cloned().map(Json::Object);
    if let Some(schema) = &declaration.entity_state_schema {
        let calculations: Vec<Json> =
            declaration.initial_state_calculations.iter().cloned().map(Json::Object).collect();
        let validation = declaration.initial_state_validation.clone().map(Json::Object);
        let validate_state =
            if creating { materialize_initial_character_state } else { verify_initial_character_state };
        state_value = validate_state(
            &participant_json,
            &state_value,
            schema,
            &calculations,
            validation.as_ref(),
            setting.as_ref(),
        )?;
    } else if creating {
        let code = "character_creation_contract_missing";
        return Err(DomainError::command_rejected_with(
            "This Ruleset has not declared complete character creation data",
            code,
            vec![RulesetDiagnostic {
                code: code.into(),
                phase: "character_initial_state".into(),
                message: "The contained Ruleset needs a complete starting state declaration.".into(),
                path: Some("participant_artifacts.entity_state_schema".into()),
                guidance: Some(
                    "Explicitly update the Setting's contained Ruleset before creation. Declare authored inputs \
                     and calculated outputs; do not guess defaults."
                        .into(),
                ),
                ..Default::default()
            }],
        ));
    }
    let mut encoded = JsonMap::new();
    encoded.insert("participant".into(), Json::Object(participant_value.clone()));
    encoded.insert("entity_state".into(), Json::Object(state_value.clone()));
    if canonical_json_bytes(&Json::Object(encoded)).len() > MAX_PARTICIPANT_BYTES {
        return Err(invalid("Reusable participant content is oversized"));
    }
    Ok((participant_value, state_value))
}

/// `_validated_authored_profile(value)`: the authored Character File body, without a prose word
/// limit.
fn validated_authored_profile(value: Option<&Json>) -> DomainResult<String> {
    let Some(Json::String(text)) = value.filter(|value| value.as_str().is_some_and(|text| !text.is_empty()))
    else {
        return Err(invalid("authored_profile must be a non-empty text string; Markdown is allowed"));
    };
    if text != pystr::strip(text) || text.contains('\0') || text.len() > MAX_PARTICIPANT_BYTES {
        return Err(invalid("Reusable participant authored profile is invalid"));
    }
    Ok(text.clone())
}

/// Whether any value sits deeper than `limit` containers, as `parse_setting_character` walks.
fn too_deep(value: &Json, depth: usize, limit: usize) -> bool {
    if depth > limit {
        return true;
    }
    match value {
        Json::Object(map) => map.values().any(|child| too_deep(child, depth + 1, limit)),
        Json::Array(items) => items.iter().any(|child| too_deep(child, depth + 1, limit)),
        _ => false,
    }
}

/// `summary` checks shared by Character Files and drafts.
fn valid_summary(summary: &str) -> bool {
    !summary.is_empty()
        && summary == pystr::strip(summary)
        && pystr::len(summary) <= 500
        && !summary.contains('\0')
}

/// `type(value) is int` for a JSON number.
fn is_int(value: &Json) -> bool {
    matches!(value, Json::Number(number) if !number.is_f64())
}

/// `parse_setting_character(source, character_id=..., role=..., ...)`: parse one exact playable
/// Character File from the selected Setting package.
#[allow(clippy::too_many_arguments)]
pub fn parse_setting_character(
    source: &str,
    character_id: &str,
    role: &str,
    package: &PackageLockEntry,
    declaration: &ParticipantArtifactDeclaration,
    campaign_setting: &PackageBinding,
    setting_character_schema: Option<&JsonMap>,
) -> DomainResult<ReusableParticipant> {
    validate_role(role)?;
    if source.is_empty()
        || source.contains('\0')
        || source.len() > MAX_PARTICIPANT_BYTES
        || character_id.is_empty()
        || pystr::len(character_id) > 160
    {
        return Err(invalid("Setting Character File input is invalid"));
    }
    let invalid_json = || invalid("Setting Character File is invalid JSON");
    let hooked = loads_hooked(source, HookPolicy { refuse_duplicates: true, refuse_constants: false })
        .map_err(|_| invalid_json())?;
    // `NaN` and `Infinity` read in Python; refused here (differences row 2).
    if hooked.non_finite || too_deep(&hooked.value, 0, 32) {
        return Err(invalid_json());
    }
    let envelope = hooked.value;
    let mut fields = vec![
        "format_id",
        "format_version",
        "id",
        "schema",
        "summary",
        "authored_profile",
        "participant",
        "entity_state",
    ];
    let format_version =
        envelope.as_object().and_then(|map| map.get("format_version")).cloned().unwrap_or(Json::Null);
    let is_current = py_eq(&format_version, &Json::from(SETTING_CHARACTER_FORMAT_VERSION));
    if is_current {
        validate_content_shape("character_file", &envelope, None)?;
        fields.push("setting_data");
    }
    let envelope_ok = envelope.as_object().is_some_and(|map| {
        map.len() == fields.len()
            && fields.iter().all(|field| map.contains_key(*field))
            && py_eq(&map["format_id"], &Json::from(SETTING_CHARACTER_FORMAT_ID))
            && is_int(&format_version)
            && (py_eq(&format_version, &Json::from(1)) || is_current)
            && py_eq(&map["id"], &Json::from(character_id))
    });
    if !envelope_ok {
        return Err(invalid("Setting Character File envelope is invalid"));
    }
    let envelope = object(envelope);
    let schema = match envelope.get("schema") {
        Some(Json::Object(schema))
            if schema.len() == 2 && schema.contains_key("id") && schema.contains_key("content_sha256") =>
        {
            schema.clone()
        }
        _ => return Err(invalid("Setting Character File schema is incompatible")),
    };
    let mut legacy_declaration = None;
    if !py_eq(&schema["id"], &Json::from(declaration.schema_id.as_str()))
        || !py_eq(&schema["content_sha256"], &Json::from(declaration.schema_sha256.as_str()))
    {
        legacy_declaration = retained_equipment_character_contract(package, declaration, &schema)?;
        if legacy_declaration.is_none() {
            return Err(invalid("Setting Character File schema is incompatible"));
        }
    }
    let summary = match &envelope["summary"] {
        Json::String(summary) if valid_summary(summary) => summary.clone(),
        _ => return Err(invalid("Setting Character File summary is invalid")),
    };
    let authored_profile = validated_authored_profile(envelope.get("authored_profile"))?;
    let empty = Json::Object(JsonMap::new());
    let setting_data = validate_setting_character_data(
        envelope.get("setting_data").unwrap_or(&empty),
        setting_character_schema,
    )?;
    let mut source_state = envelope["entity_state"].clone();
    let mut source_migration = None;
    if let Some(legacy) = &legacy_declaration {
        let (_, validated_old) = validated_payload(
            role,
            &envelope["participant"],
            &source_state,
            legacy,
            false,
            Some(&setting_data),
        )?;
        let Some(validation) = &declaration.initial_state_validation else {
            return Err(DomainError::uncaught("AttributeError", "'NoneType' object has no attribute 'get'"));
        };
        let definitions = validation.get("definitions").cloned().unwrap_or_else(|| empty.clone());
        let weapons = sylvia_domain::pyops::py_item(&definitions, "saved_weapons")?;
        let namespace = format!("{PACKAGE_ID}.entities");
        let mut characters = JsonMap::new();
        characters.insert(character_id.to_owned(), Json::Object(validated_old));
        let mut state = JsonMap::new();
        state.insert(namespace.clone(), Json::Object(characters));
        let migrated = migrate_state(&state, weapons)?;
        source_state = migrated[&namespace][character_id].clone();
        let mut migration = JsonMap::new();
        migration.insert("migration_id".into(), Json::from(MIGRATION_ID));
        migration.insert("source_content_sha256".into(), Json::from(sha256_hex(source.as_bytes())));
        migration.insert("source_schema_sha256".into(), schema["content_sha256"].clone());
        migration.insert("target_schema_sha256".into(), Json::from(declaration.schema_sha256.as_str()));
        source_migration = Some(migration);
    }
    let (participant, entity_state) = validated_payload(
        role,
        &envelope["participant"],
        &source_state,
        declaration,
        false,
        Some(&setting_data),
    )?;
    let name = match participant.get("name") {
        Some(Json::String(name))
            if !name.is_empty() && name == pystr::strip(name) && pystr::len(name) <= 160 =>
        {
            name.clone()
        }
        _ => return Err(invalid("Setting Character File name is invalid")),
    };
    let mut canonical_envelope = JsonMap::new();
    canonical_envelope.insert("format_id".into(), Json::from(SETTING_CHARACTER_FORMAT_ID));
    canonical_envelope.insert("format_version".into(), format_version);
    canonical_envelope.insert("id".into(), Json::from(character_id));
    let mut schema_binding = JsonMap::new();
    schema_binding.insert("id".into(), Json::from(declaration.schema_id.as_str()));
    schema_binding.insert("content_sha256".into(), Json::from(declaration.schema_sha256.as_str()));
    canonical_envelope.insert("schema".into(), Json::Object(schema_binding));
    canonical_envelope.insert("summary".into(), Json::from(summary.as_str()));
    canonical_envelope.insert("authored_profile".into(), Json::from(authored_profile.as_str()));
    canonical_envelope.insert("participant".into(), Json::Object(participant.clone()));
    canonical_envelope.insert("entity_state".into(), Json::Object(entity_state.clone()));
    if is_current {
        canonical_envelope.insert("setting_data".into(), Json::Object(setting_data.clone()));
    }
    if let Some(migration) = &source_migration {
        canonical_envelope.insert("source_migration".into(), Json::Object(migration.clone()));
    }
    let encoded = canonical_json_bytes(&Json::Object(canonical_envelope));
    if encoded.len() > MAX_PARTICIPANT_BYTES {
        return Err(invalid("Setting Character File content is oversized"));
    }
    Ok(ReusableParticipant {
        artifact_id: character_id.to_owned(),
        role: role.to_owned(),
        name,
        summary,
        authored_profile: Some(authored_profile),
        content_sha256: sha256_hex(&encoded),
        package_id: package.package_id().to_owned(),
        package_version: package.version().to_owned(),
        package_sha256: package.sha256().to_owned(),
        schema_id: declaration.schema_id.clone(),
        schema_sha256: declaration.schema_sha256.clone(),
        campaign_setting_id: campaign_setting.package_id.clone(),
        campaign_setting_version: campaign_setting.version.clone(),
        campaign_setting_sha256: campaign_setting.content_sha256.clone(),
        participant,
        entity_state,
        setting_data,
        source_profile: None,
        source_migration,
        builtin_starter: false,
    })
}

/// A checked draft: the authored profile, participant, complete entity state and setting data.
pub type ParticipantDraft = (String, JsonMap, JsonMap, JsonMap);

/// `validate_reusable_participant_draft(...)`: check authored input and calculate a complete
/// character without saving or campaign access.
#[allow(clippy::too_many_arguments)]
pub fn validate_reusable_participant_draft(
    role: &str,
    summary: &str,
    authored_profile: &Json,
    participant: &Json,
    entity_state: &Json,
    declaration: &ParticipantArtifactDeclaration,
    setting_data: Option<&Json>,
    setting_character_schema: Option<&JsonMap>,
) -> DomainResult<ParticipantDraft> {
    if !valid_summary(summary) {
        return Err(invalid("Reusable participant metadata is invalid"));
    }
    let profile = validated_authored_profile(Some(authored_profile))?;
    let empty = Json::Object(JsonMap::new());
    let setting = validate_setting_character_data(setting_data.unwrap_or(&empty), setting_character_schema)?;
    let (participant_value, state_value) =
        validated_payload(role, participant, entity_state, declaration, true, Some(&setting))?;
    let name_ok = matches!(participant_value.get("name"),
        Some(Json::String(name)) if name == pystr::strip(name) && !name.is_empty() && pystr::len(name) <= 160);
    if !name_ok {
        return Err(invalid("Reusable participant name is invalid"));
    }
    let payload = payload_of(&participant_value, &state_value, Some(&setting));
    if canonical_json_bytes(&payload).len() + profile.len() > MAX_PARTICIPANT_BYTES {
        return Err(invalid("Reusable participant content is oversized"));
    }
    Ok((profile, participant_value, state_value, setting))
}

fn payload_of(participant: &JsonMap, entity_state: &JsonMap, setting_data: Option<&JsonMap>) -> Json {
    let mut payload = JsonMap::new();
    payload.insert("participant".into(), Json::Object(participant.clone()));
    payload.insert("entity_state".into(), Json::Object(entity_state.clone()));
    if let Some(setting_data) = setting_data {
        payload.insert("setting_data".into(), Json::Object(setting_data.clone()));
    }
    Json::Object(payload)
}

fn insert_bindings(
    map: &mut JsonMap,
    package: &PackageLockEntry,
    declaration: &ParticipantArtifactDeclaration,
    campaign_setting: Option<&PackageBinding>,
) {
    map.insert("package_id".into(), Json::from(package.package_id()));
    map.insert("package_version".into(), Json::from(package.version()));
    map.insert("package_sha256".into(), Json::from(package.sha256()));
    map.insert("schema_id".into(), Json::from(declaration.schema_id.as_str()));
    map.insert("schema_sha256".into(), Json::from(declaration.schema_sha256.as_str()));
    if let Some(setting) = campaign_setting {
        map.insert("campaign_setting_id".into(), Json::from(setting.package_id.as_str()));
        map.insert("campaign_setting_version".into(), Json::from(setting.version.as_str()));
        map.insert("campaign_setting_sha256".into(), Json::from(setting.content_sha256.as_str()));
    }
}

/// `save_reusable_participant(...)`: validate and persist one immutable owner-local participant
/// artifact.
#[allow(clippy::too_many_arguments)]
pub fn save_reusable_participant(
    storage_root: &Path,
    operation_id: &str,
    role: &str,
    summary: &str,
    authored_profile: &Json,
    participant: &Json,
    entity_state: &Json,
    package: &PackageLockEntry,
    declaration: &ParticipantArtifactDeclaration,
    campaign_setting: &PackageBinding,
    setting_data: Option<&Json>,
    setting_character_schema: Option<&JsonMap>,
    source_profile: Option<&Json>,
) -> DomainResult<ReusableParticipant> {
    if operation_id.is_empty() {
        return Err(invalid("Reusable participant operation identity is invalid"));
    }
    let (profile, participant_value, state_value, setting_value) = validate_reusable_participant_draft(
        role,
        summary,
        authored_profile,
        participant,
        entity_state,
        declaration,
        setting_data,
        setting_character_schema,
    )?;
    let name = py_str(&participant_value["name"]);
    let source_profile = validate_source_profile(source_profile.unwrap_or(&Json::Null))?;
    let source_profile_json = source_profile.clone().map_or(Json::Null, Json::Object);
    let payload = payload_of(&participant_value, &state_value, Some(&setting_value));
    let mut identity = JsonMap::new();
    identity.insert("role".into(), Json::from(role));
    identity.insert("source_profile".into(), source_profile_json.clone());
    identity.insert("summary".into(), Json::from(summary));
    identity.insert("authored_profile".into(), Json::from(profile.as_str()));
    insert_bindings(&mut identity, package, declaration, Some(campaign_setting));
    identity.insert("payload".into(), payload.clone());
    let content_sha256 = sha256_hex(canonical_json_bytes(&Json::Object(identity)));
    let operation_suffix = &sha256_hex(operation_id.as_bytes())[..24];
    let artifact_id = format!("participant-{operation_suffix}-{}", &content_sha256[..16]);
    let mut record = JsonMap::new();
    record.insert("format_id".into(), Json::from(REUSABLE_PARTICIPANT_FORMAT_ID));
    record.insert("format_version".into(), Json::from(REUSABLE_PARTICIPANT_FORMAT_VERSION));
    record.insert("artifact_id".into(), Json::from(artifact_id.as_str()));
    record.insert("source_profile".into(), source_profile_json);
    record.insert("role".into(), Json::from(role));
    record.insert("name".into(), Json::from(name.as_str()));
    record.insert("summary".into(), Json::from(summary));
    record.insert("content_sha256".into(), Json::from(content_sha256.as_str()));
    insert_bindings(&mut record, package, declaration, Some(campaign_setting));
    let target = library_root(storage_root).join(&artifact_id);
    let load = || {
        load_reusable_participant(
            storage_root,
            &artifact_id,
            package,
            declaration,
            campaign_setting,
            None,
            setting_character_schema,
        )
    };
    let _guard = LIBRARY_WRITE_LOCK.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    if target.exists() {
        let existing = load()?;
        if existing.content_sha256 != content_sha256 {
            return Err(invalid("Reusable participant identity conflicts"));
        }
        return Ok(existing);
    }
    let parent = target.parent().expect("inside the library");
    std::fs::create_dir_all(parent).map_err(|error| os_error(&error, parent))?;
    // Make the immutable entry visible only after every file is ready. Other chats can refresh
    // the library during a save without observing a partial artifact.
    let temporary = storage_root.join(temporary_name(".participant-save-", ""));
    std::fs::create_dir(&temporary).map_err(|error| os_error(&error, &temporary))?;
    let staged = temporary.join(&artifact_id);
    let written = (|| {
        std::fs::create_dir(&staged).map_err(|error| os_error(&error, &staged))?;
        atomic_write(&staged.join("profile.md"), profile.as_bytes())?;
        atomic_write(&staged.join("payload.json"), &canonical_json_bytes(&payload))?;
        atomic_write(&staged.join("record.json"), &canonical_json_bytes(&Json::Object(record)))?;
        std::fs::rename(&staged, &target).map_err(|error| os_error(&error, &staged))
    })();
    let cleaned = std::fs::remove_dir_all(&temporary).map_err(|error| os_error(&error, &temporary));
    written?;
    cleaned?;
    load()
}

/// `delete_reusable_participant(storage_root, artifact_id, expected_content_sha256=...,
/// confirmation=...)`: delete one exact local participant after fencing against a stale catalog
/// choice.
pub fn delete_reusable_participant(
    storage_root: &Path,
    artifact_id: &str,
    expected_content_sha256: &str,
    confirmation: &str,
) -> DomainResult<bool> {
    if !is_artifact_id(artifact_id) {
        return Err(invalid("Reusable participant identity is invalid"));
    }
    if !lower_hex(expected_content_sha256, 64) {
        return Err(invalid("Reusable participant version is invalid"));
    }
    if confirmation != format!("permanently delete {artifact_id}") {
        return Err(invalid("Reusable participant deletion confirmation is not exact"));
    }
    let _guard = LIBRARY_WRITE_LOCK.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    let Some(target) = validated_library_entry(storage_root, LIBRARY_NAME, artifact_id)? else {
        return Ok(false);
    };
    validate_owned_files(&target, &OWNED_NAMES)?;
    let Json::Object(record) = loads(&read_text(&target.join("record.json"))?)? else {
        return Err(invalid("Reusable participant record is invalid"));
    };
    if !record.get("artifact_id").is_some_and(|value| py_eq(value, &Json::from(artifact_id))) {
        return Err(invalid("Reusable participant record is invalid"));
    }
    if !record.get("content_sha256").is_some_and(|value| py_eq(value, &Json::from(expected_content_sha256))) {
        return Err(invalid("Reusable participant changed; refresh the library and try again"));
    }
    delete_exact_library_entry(storage_root, LIBRARY_NAME, artifact_id, &OWNED_NAMES)
}

const COMMON_FIELDS: [&str; 12] = [
    "format_id",
    "format_version",
    "artifact_id",
    "role",
    "name",
    "summary",
    "content_sha256",
    "package_id",
    "package_version",
    "package_sha256",
    "schema_id",
    "schema_sha256",
];
const SETTING_FIELDS: [&str; 3] =
    ["campaign_setting_id", "campaign_setting_version", "campaign_setting_sha256"];

fn exact_keys(map: &JsonMap, fields: &[&str]) -> bool {
    map.len() == fields.len() && fields.iter().all(|field| map.contains_key(*field))
}

fn text_eq(value: &Json, text: &str) -> bool {
    py_eq(value, &Json::from(text))
}

/// `load_reusable_participant(storage_root, artifact_id, package=..., declaration=...,
/// campaign_setting=..., legacy_campaign_setting=..., setting_character_schema=...)`: load and
/// revalidate one exact local participant without trusting its record.
pub fn load_reusable_participant(
    storage_root: &Path,
    artifact_id: &str,
    package: &PackageLockEntry,
    declaration: &ParticipantArtifactDeclaration,
    campaign_setting: &PackageBinding,
    legacy_campaign_setting: Option<&PackageBinding>,
    setting_character_schema: Option<&JsonMap>,
) -> DomainResult<ReusableParticipant> {
    if !is_artifact_id(artifact_id) {
        return Err(invalid("Reusable participant identity is invalid"));
    }
    let root = library_root(storage_root).join(artifact_id);
    let Json::Object(record) = loads(&read_text(&root.join("record.json"))?)? else {
        return Err(invalid("Reusable participant record is invalid"));
    };
    let format_version = match record.get("format_version") {
        // An integer beyond `i64` is no supported version: incompatible, as in Python.
        Some(Json::Number(number)) if !number.is_f64() => Some(number.as_i64().unwrap_or(i64::MAX)),
        _ => None,
    };
    let Some(format_version) = format_version else {
        return Err(invalid("Reusable participant record version is invalid"));
    };
    if format_version == 1 {
        if !exact_keys(&record, &COMMON_FIELDS) || legacy_campaign_setting != Some(campaign_setting) {
            return Err(invalid(INCOMPATIBLE));
        }
    } else if (2..=REUSABLE_PARTICIPANT_FORMAT_VERSION).contains(&format_version) {
        let mut fields: Vec<&str> = COMMON_FIELDS.iter().chain(&SETTING_FIELDS).copied().collect();
        if format_version == 5 {
            fields.push("source_profile");
        }
        if !exact_keys(&record, &fields) {
            return Err(invalid("Reusable participant record is invalid"));
        }
        if !text_eq(&record["campaign_setting_id"], &campaign_setting.package_id)
            || !text_eq(&record["campaign_setting_version"], &campaign_setting.version)
            || !text_eq(&record["campaign_setting_sha256"], &campaign_setting.content_sha256)
        {
            return Err(invalid(INCOMPATIBLE));
        }
    } else {
        return Err(invalid(INCOMPATIBLE));
    }
    if !text_eq(&record["format_id"], REUSABLE_PARTICIPANT_FORMAT_ID)
        || !text_eq(&record["artifact_id"], artifact_id)
        || !text_eq(&record["package_id"], package.package_id())
        || !text_eq(&record["package_version"], package.version())
        || !text_eq(&record["package_sha256"], package.sha256())
        || !text_eq(&record["schema_id"], &declaration.schema_id)
        || !text_eq(&record["schema_sha256"], &declaration.schema_sha256)
    {
        return Err(invalid(INCOMPATIBLE));
    }
    let payload = loads(&read_text(&root.join("payload.json"))?)?;
    let with_setting = format_version >= 4;
    let payload_fields: &[&str] = if with_setting {
        &["participant", "entity_state", "setting_data"]
    } else {
        &["participant", "entity_state"]
    };
    let Json::Object(payload) = payload.clone() else {
        return Err(invalid("Reusable participant payload is invalid"));
    };
    if !exact_keys(&payload, payload_fields) {
        return Err(invalid("Reusable participant payload is invalid"));
    }
    let empty = Json::Object(JsonMap::new());
    let setting_data = validate_setting_character_data(
        payload.get("setting_data").unwrap_or(&empty),
        setting_character_schema,
    )?;
    let role = py_str(&record["role"]);
    let (participant, entity_state) = validated_payload(
        &role,
        &payload["participant"],
        &payload["entity_state"],
        declaration,
        false,
        Some(&setting_data),
    )?;
    let authored_profile = if format_version >= 3 {
        let text = pystr::utf8_decode(&read_bytes(&root.join("profile.md"))?).map_err(unicode_error)?;
        Some(validated_authored_profile(Some(&Json::from(text)))?)
    } else {
        None
    };
    if let Some(profile) = &authored_profile {
        if canonical_json_bytes(&Json::Object(payload.clone())).len() + profile.len() > MAX_PARTICIPANT_BYTES
        {
            return Err(invalid("Reusable participant content is oversized"));
        }
    }
    let mut identity = JsonMap::new();
    identity.insert("role".into(), Json::from(role.as_str()));
    identity.insert("summary".into(), record["summary"].clone());
    insert_bindings(&mut identity, package, declaration, None);
    let identity_setting = with_setting.then_some(&setting_data);
    identity.insert("payload".into(), payload_of(&participant, &entity_state, identity_setting));
    if let Some(profile) = &authored_profile {
        identity.insert("authored_profile".into(), Json::from(profile.as_str()));
    }
    let source_profile = validate_source_profile(record.get("source_profile").unwrap_or(&Json::Null))?;
    if format_version == 5 {
        identity.insert("source_profile".into(), source_profile.clone().map_or(Json::Null, Json::Object));
    }
    if format_version >= 2 {
        insert_bindings(&mut identity, package, declaration, Some(campaign_setting));
    }
    let content_sha256 = sha256_hex(canonical_json_bytes(&Json::Object(identity)));
    let summary_text = py_str(&record["summary"]);
    let name = participant.get("name").and_then(Json::as_str).map(str::to_owned);
    let consistent = text_eq(&record["content_sha256"], &content_sha256)
        && name.as_deref().is_some_and(|name| text_eq(&record["name"], name))
        && record["summary"].is_string()
        && !pystr::strip(&summary_text).is_empty()
        && summary_text == pystr::strip(&summary_text)
        && pystr::len(&summary_text) <= 500;
    if !consistent {
        return Err(invalid("Reusable participant content hash is invalid"));
    }
    Ok(ReusableParticipant {
        artifact_id: artifact_id.to_owned(),
        role,
        name: name.expect("checked above"),
        summary: summary_text,
        authored_profile,
        content_sha256,
        package_id: package.package_id().to_owned(),
        package_version: package.version().to_owned(),
        package_sha256: package.sha256().to_owned(),
        schema_id: declaration.schema_id.clone(),
        schema_sha256: declaration.schema_sha256.clone(),
        campaign_setting_id: campaign_setting.package_id.clone(),
        campaign_setting_version: campaign_setting.version.clone(),
        campaign_setting_sha256: campaign_setting.content_sha256.clone(),
        participant,
        entity_state,
        setting_data,
        source_profile,
        source_migration: None,
        builtin_starter: false,
    })
}

/// `compatible_reusable_participants(storage_root, ...)`: list participants compatible with one
/// exact Setting and Ruleset schema; an incompatible record is skipped silently.
#[allow(clippy::too_many_arguments)]
pub fn compatible_reusable_participants(
    storage_root: &Path,
    package: &PackageLockEntry,
    declaration: &ParticipantArtifactDeclaration,
    campaign_setting: &PackageBinding,
    legacy_campaign_setting: Option<&PackageBinding>,
    setting_character_schema: Option<&JsonMap>,
    mut on_invalid: Option<InvalidHandler<'_>>,
) -> DomainResult<Vec<ReusableParticipant>> {
    let root = library_root(storage_root);
    if !root.exists() {
        return Ok(Vec::new());
    }
    let mut names = Vec::new();
    for entry in std::fs::read_dir(&root).map_err(|error| os_error(&error, &root))? {
        let path = entry.map_err(|error| os_error(&error, &root))?.path();
        if path.is_dir() {
            names.push(path.file_name().expect("an entry").to_string_lossy().into_owned());
        }
    }
    if names.len() > MAX_LIBRARY_PARTICIPANTS {
        return Err(invalid("Reusable participant library is oversized"));
    }
    names.sort();
    let mut compatible = Vec::new();
    for name in names {
        let loaded = load_reusable_participant(
            storage_root,
            &name,
            package,
            declaration,
            campaign_setting,
            legacy_campaign_setting,
            setting_character_schema,
        );
        match loaded {
            Ok(participant) => compatible.push(participant),
            Err(error) if catalog_catches(&error) => {
                if error.message() == INCOMPATIBLE {
                    continue;
                }
                match on_invalid.as_mut() {
                    Some(handler) => handler(&name, &error),
                    None => return Err(error),
                }
            }
            Err(error) => return Err(error),
        }
    }
    Ok(compatible)
}
