//! `domain/campaign_plan.py`: versioned structured Campaign Plan artifacts used by Builder and
//! campaign handoff.

use crate::authority::{canonical_payload_is_safe, validate_authority_identity_value};
use crate::campaign_ending::validate_ending;
use crate::content::validate_content_shape;
use crate::error::{DomainError, DomainResult, RulesetDiagnostic};
use crate::no_ruleset_plan::{is_no_ruleset_plan, validate_no_ruleset_world};
use crate::package_library::{canonical_json_bytes, validate_portable_path};
use crate::pyops::py_eq;
use serde_json::json;
use std::collections::BTreeSet;
use sylvia_base::hash::sha256_hex;
use sylvia_base::pystr;
use sylvia_base::{Json, JsonMap};

/// `CAMPAIGN_PLAN_FORMAT_ID`.
pub const CAMPAIGN_PLAN_FORMAT_ID: &str = "sylvia-gm-campaign-plan";
/// `CAMPAIGN_PLAN_FORMAT_VERSION`.
pub const CAMPAIGN_PLAN_FORMAT_VERSION: i64 = 2;
/// `CAMPAIGN_PLAN_BYTE_LIMIT`.
pub const CAMPAIGN_PLAN_BYTE_LIMIT: usize = 512 * 1024;
/// `CAMPAIGN_PLAN_REFERENCE_LIMIT`.
pub const CAMPAIGN_PLAN_REFERENCE_LIMIT: usize = 512;

const PRIVATE_KEYS: [&str; 7] = [
    "api_key",
    "authorization",
    "credential",
    "password",
    "private_reasoning",
    "refresh_token",
    "secret_key",
];

/// `PackageBinding`: one exact package identity embedded in a Campaign Plan artifact.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PackageBinding {
    pub package_id: String,
    pub version: String,
    pub content_sha256: String,
}

impl PackageBinding {
    /// `as_payload()`.
    pub fn as_payload(&self) -> JsonMap {
        object(json!({"id": self.package_id, "version": self.version, "content_sha256": self.content_sha256}))
    }
}

/// `ValidatedCampaignPlan`: canonical validated plan bytes plus its deterministic identity.
#[derive(Debug, Clone, PartialEq)]
pub struct ValidatedCampaignPlan {
    pub payload: JsonMap,
    pub content_sha256: String,
    pub artifact_id: String,
    pub title: String,
    pub readable_markdown: String,
    pub referenced_setting_paths: Vec<String>,
}

/// `CampaignPlanCharacterReference`: one exact Campaign Setting character.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CampaignPlanCharacterReference {
    pub character_id: String,
    pub path: String,
}

impl CampaignPlanCharacterReference {
    /// `as_payload()`.
    pub fn as_payload(&self) -> JsonMap {
        object(json!({"id": self.character_id, "path": self.path}))
    }
}

/// `StartingPartyPolicy`: the Plan's narrowing of the Ruleset's compatible party envelope.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StartingPartyPolicy {
    pub player_character_mode: String,
    pub fixed_player_character: Option<CampaignPlanCharacterReference>,
    pub required_companions: Vec<CampaignPlanCharacterReference>,
    pub custom_companion_minimum: i64,
    pub custom_companion_maximum: i64,
}

impl StartingPartyPolicy {
    /// `as_payload()`.
    pub fn as_payload(&self) -> JsonMap {
        let mut player = JsonMap::new();
        player.insert("mode".into(), Json::from(self.player_character_mode.as_str()));
        if let Some(fixed) = &self.fixed_player_character {
            player.insert("character".into(), Json::Object(fixed.as_payload()));
        }
        object(json!({
            "player_character": player,
            "required_companions": self.required_companions.iter().map(|item| item.as_payload()).collect::<Vec<_>>(),
            "custom_companions": {"minimum": self.custom_companion_minimum, "maximum": self.custom_companion_maximum},
        }))
    }
}

/// The inputs `validate_campaign_plan` checks the Plan against.
#[derive(Debug, Clone, Copy)]
pub struct CampaignPlanExpectations<'a> {
    pub expected_ruleset: Option<&'a PackageBinding>,
    pub expected_setting: Option<&'a PackageBinding>,
    pub expected_closure_sha256: &'a str,
    pub setting_paths: &'a BTreeSet<String>,
    pub setting_knowledge_ids: &'a BTreeSet<String>,
}

fn object(value: Json) -> JsonMap {
    match value {
        Json::Object(map) => map,
        _ => unreachable!("built as an object"),
    }
}

fn value_error(message: impl Into<String>) -> DomainError {
    DomainError::value(message)
}

/// `_object(value, label)`: JSON keys are always text, so only the type is checked.
fn as_object<'a>(value: &'a Json, label: &str) -> DomainResult<&'a JsonMap> {
    value.as_object().ok_or_else(|| value_error(format!("{label} must be an object")))
}

/// `re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name)`.
fn safe_field_name(name: &str) -> bool {
    let bytes = name.as_bytes();
    !bytes.is_empty()
        && bytes.len() <= 64
        && (bytes[0].is_ascii_alphabetic() || bytes[0] == b'_')
        && bytes.iter().all(|b| b.is_ascii_alphanumeric() || *b == b'_')
}

/// `CampaignPlanFieldsError(value, keys, label, optional=..., path=...)`.
pub fn fields_error(
    value: &JsonMap,
    keys: &[&str],
    label: &str,
    optional: &[&str],
    path: Option<&str>,
) -> DomainError {
    let actual: Vec<&str> = value.keys().map(String::as_str).collect();
    let missing: Vec<&str> = keys.iter().copied().filter(|key| !actual.contains(key)).collect();
    let unexpected: Vec<&str> =
        actual.iter().copied().filter(|name| !keys.contains(name) && !optional.contains(name)).collect();
    let mut details = Vec::new();
    for (kind, names) in [("missing", missing), ("unexpected", unexpected)] {
        if names.is_empty() {
            continue;
        }
        let mut safe: Vec<&str> = names
            .iter()
            .map(|name| if safe_field_name(name) { *name } else { "[invalid field name]" })
            .collect();
        safe.sort_unstable();
        safe.truncate(8);
        details.push(format!("{kind}: {}", safe.join(", ")));
    }
    let message = format!("{label} fields are invalid ({})", details.join("; "));
    DomainError::Diagnosed {
        error_type: "CampaignPlanFieldsError",
        message: message.clone(),
        diagnostics: vec![RulesetDiagnostic {
            code: "campaign_plan_fields_invalid".into(),
            phase: "campaign_plan".into(),
            message,
            path: path.map(str::to_owned),
            guidance: Some(
                "Correct the named fields using the Campaign Plan reference. Preserve unrelated draft content and \
                 the original source bindings."
                    .into(),
            ),
            ..Default::default()
        }],
    }
}

/// `CampaignPlanReferenceError(label, path=...)`.
fn reference_error(label: &str, path: Option<&str>) -> DomainError {
    let message = format!("{label} must be an object with id and path fields");
    DomainError::Diagnosed {
        error_type: "CampaignPlanReferenceError",
        message: message.clone(),
        diagnostics: vec![RulesetDiagnostic {
            code: "campaign_plan_reference_invalid".into(),
            phase: "campaign_plan".into(),
            message,
            path: path.map(str::to_owned),
            guidance: Some(
                "Use the {id, path} reference object shown in the Campaign Plan reference. Correct the reference \
                 at this JSON path and preserve unrelated data."
                    .into(),
            ),
            ..Default::default()
        }],
    }
}

/// `_exact(value, keys, label, optional=..., path=...)`: a closed Plan object's fields.
fn exact(
    value: &JsonMap,
    keys: &[&str],
    label: &str,
    optional: &[&str],
    path: Option<&str>,
) -> DomainResult<()> {
    let missing = keys.iter().any(|key| !value.contains_key(*key));
    let extra = value.keys().any(|key| !keys.contains(&key.as_str()) && !optional.contains(&key.as_str()));
    if missing || extra {
        return Err(fields_error(value, keys, label, optional, path));
    }
    Ok(())
}

/// `_text(value, label, maximum=...)`.
fn text(value: &Json, label: &str, maximum: usize) -> DomainResult<String> {
    match value {
        Json::String(text)
            if !pystr::strip(text).is_empty()
                && text == pystr::strip(text)
                && pystr::len(text) <= maximum
                && !text.contains('\0') =>
        {
            Ok(text.clone())
        }
        _ => Err(value_error(format!("{label} is invalid"))),
    }
}

/// `_texts(value, label, minimum=..., maximum=...)`.
fn texts(value: &Json, label: &str, minimum: usize, maximum: usize) -> DomainResult<Vec<String>> {
    let Some(items) = value.as_array().filter(|items| (minimum..=maximum).contains(&items.len())) else {
        return Err(value_error(format!("{label} is invalid")));
    };
    items.iter().map(|item| text(item, label, 4_000)).collect()
}

/// `re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", text)`.
fn identity_shape(text: &str) -> bool {
    let bytes = text.as_bytes();
    !bytes.is_empty()
        && bytes[0].is_ascii_alphanumeric()
        && bytes.iter().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b':' | b'-'))
}

/// `_identity(value, label)`.
fn identity(value: &Json, label: &str) -> DomainResult<String> {
    let text = text(value, label, 160)?;
    if !identity_shape(&text) {
        return Err(value_error(format!("{label} is invalid")));
    }
    Ok(text)
}

/// `re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*", key)`.
fn state_path_shape(key: &str) -> bool {
    key.split('.').all(|part| {
        !part.is_empty() && part.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'-'))
    })
}

/// `StateValue(namespace, key, value)`: both identities validated, in that order.
fn state_value(entry: &JsonMap) -> DomainResult<(&str, &str, &Json)> {
    let namespace = validate_authority_identity_value(&entry["namespace"], "State namespace", false)?;
    let key = validate_authority_identity_value(&entry["key"], "State key", false)?;
    Ok((namespace, key, &entry["value"]))
}

fn belongs_to(namespace: &str, ruleset_id: &str) -> bool {
    namespace == ruleset_id || namespace.starts_with(&format!("{ruleset_id}."))
}

/// `template(item)` of `_validate_party_placements`: `{"$participant": "id" | "name"}` markers.
fn placement_template(item: &Json) -> DomainResult<()> {
    match item {
        Json::Object(map) if map.contains_key("$participant") => {
            if map.len() != 1 || !matches!(map["$participant"].as_str(), Some("id" | "name")) {
                return Err(value_error("Party placement participant reference is invalid"));
            }
            Ok(())
        }
        Json::Object(map) => map.values().try_for_each(placement_template),
        Json::Array(items) => items.iter().try_for_each(placement_template),
        _ => Ok(()),
    }
}

/// `_validate_party_placements(value, ruleset_id)`: ordered party slots whose data belongs
/// entirely to the selected Ruleset.
fn validate_party_placements(value: &Json, ruleset_id: &str) -> DomainResult<()> {
    let Some(slots) = value.as_array().filter(|slots| (1..=64).contains(&slots.len())) else {
        return Err(value_error("Party placements must contain one to 64 ordered slots"));
    };
    for raw in slots {
        let slot = as_object(raw, "Party placement")?;
        exact(slot, &["entity_state", "append"], "Party placement", &[], None)?;
        let entity_state = as_object(&slot["entity_state"], "Party placement entity state")?;
        if entity_state.contains_key("$participant") {
            return Err(value_error("Party placement entity state must remain an object"));
        }
        let Some(appends) = slot["append"].as_array().filter(|appends| appends.len() <= 16) else {
            return Err(value_error("Party placement append list is invalid"));
        };
        for raw_append in appends {
            let entry = as_object(raw_append, "Party placement append")?;
            exact(entry, &["namespace", "key", "value"], "Party placement append", &[], None)?;
            let (namespace, key, value) = state_value(entry)?;
            if !belongs_to(namespace, ruleset_id) {
                return Err(value_error("Party placement state must belong to the Plan's Ruleset"));
            }
            if !state_path_shape(key) {
                return Err(value_error("Party placement state path is invalid"));
            }
            let mut nested = value.clone();
            for field in key.split('.').rev() {
                let mut wrapper = JsonMap::new();
                wrapper.insert(field.to_owned(), nested);
                nested = Json::Object(wrapper);
            }
            if !canonical_payload_is_safe(&nested) {
                return Err(value_error("Party placement contains a reserved canonical channel"));
            }
        }
        if !canonical_payload_is_safe(raw) {
            return Err(value_error("Party placement contains a reserved canonical channel"));
        }
        placement_template(raw)?;
    }
    Ok(())
}

/// `_package_binding(value, label)`.
fn package_binding(value: &Json, label: &str) -> DomainResult<PackageBinding> {
    let item = as_object(value, label)?;
    exact(item, &["id", "version", "content_sha256"], label, &[], None)?;
    let content_sha256 = text(&item["content_sha256"], &format!("{label} content hash"), 64)?;
    if content_sha256.len() != 64 || !content_sha256.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f')) {
        return Err(value_error(format!("{label} content hash is invalid")));
    }
    Ok(PackageBinding {
        package_id: identity(&item["id"], &format!("{label} identity"))?,
        version: identity(&item["version"], &format!("{label} version"))?,
        content_sha256,
    })
}

/// `_setting_reference(value, label, path=...)`: a portable Setting path.
fn setting_reference(value: &Json, label: &str, path: Option<&str>) -> DomainResult<String> {
    let Json::Object(item) = value else { return Err(reference_error(label, path)) };
    exact(item, &["id", "path"], label, &[], path)?;
    identity(&item["id"], &format!("{label} identity"))?;
    let reference = text(&item["path"], &format!("{label} path"), 512)?;
    Ok(validate_portable_path(&reference)?.to_owned())
}

/// `_character_reference(value, label, path=...)`.
fn character_reference(
    value: &Json,
    label: &str,
    path: &str,
) -> DomainResult<CampaignPlanCharacterReference> {
    let item = as_object(value, label)?;
    exact(item, &["id", "path"], label, &[], Some(path))?;
    let character_id = identity(&item["id"], &format!("{label} identity"))?;
    let reference = text(&item["path"], &format!("{label} path"), 512)?;
    Ok(CampaignPlanCharacterReference { character_id, path: validate_portable_path(&reference)?.to_owned() })
}

/// `parse_starting_party_policy(value)`: the portable starting-party policy.
pub fn parse_starting_party_policy(value: &Json) -> DomainResult<StartingPartyPolicy> {
    let party = as_object(value, "Starting party")?;
    exact(
        party,
        &["custom_companions", "player_character", "required_companions"],
        "Starting party",
        &[],
        Some("$.starting_party"),
    )?;
    let player = as_object(&party["player_character"], "Starting player character")?;
    let mode = text(player.get("mode").unwrap_or(&Json::Null), "Starting player character mode", 20)?;
    let fixed = match mode.as_str() {
        "compatible" => {
            exact(player, &["mode"], "Starting player character", &[], None)?;
            None
        }
        "fixed" => {
            exact(player, &["character", "mode"], "Starting player character", &[], None)?;
            Some(character_reference(
                &player["character"],
                "Fixed player character",
                "$.starting_party.player_character.character",
            )?)
        }
        _ => return Err(value_error("Starting player character mode is invalid")),
    };
    let Some(required) = party["required_companions"].as_array().filter(|items| items.len() <= 64) else {
        return Err(value_error("Required companion references are invalid"));
    };
    let mut required_companions = Vec::with_capacity(required.len());
    for (index, item) in required.iter().enumerate() {
        required_companions.push(character_reference(
            item,
            "Required companion reference",
            &format!("$.starting_party.required_companions[{index}]"),
        )?);
    }
    let mut identities: Vec<(&str, &str)> =
        required_companions.iter().map(|item| (item.character_id.as_str(), item.path.as_str())).collect();
    let count = identities.len();
    identities.sort_unstable();
    identities.dedup();
    if identities.len() != count {
        return Err(value_error("Required companion references must be unique"));
    }
    let custom = as_object(&party["custom_companions"], "Custom companion policy")?;
    exact(custom, &["maximum", "minimum"], "Custom companion policy", &[], None)?;
    let int = |value: &Json| match value {
        Json::Number(number) if number.is_i64() || number.is_u64() => {
            Some(number.as_i64().unwrap_or(i64::MAX))
        }
        _ => None,
    };
    let (Some(minimum), Some(maximum)) = (int(&custom["minimum"]), int(&custom["maximum"])) else {
        return Err(value_error("Custom companion limits are invalid"));
    };
    if !(0 <= minimum && minimum <= maximum && maximum <= 64) {
        return Err(value_error("Custom companion limits are invalid"));
    }
    Ok(StartingPartyPolicy {
        player_character_mode: mode,
        fixed_player_character: fixed,
        required_companions,
        custom_companion_minimum: minimum,
        custom_companion_maximum: maximum,
    })
}

/// `_ABSOLUTE_PATH.match(value)`: `^[A-Za-z]:[\\/]`, `^/` or `^\\\\`.
fn absolute_path(value: &str) -> bool {
    let bytes = value.as_bytes();
    (bytes.len() >= 3
        && bytes[0].is_ascii_alphabetic()
        && bytes[1] == b':'
        && matches!(bytes[2], b'\\' | b'/'))
        || value.starts_with('/')
        || value.starts_with("\\\\")
}

/// `_scan_private(value, path)`: credential-shaped fields and machine-private absolute paths.
fn scan_private(value: &Json, path: &str) -> DomainResult<()> {
    match value {
        Json::Object(map) => {
            for (key, item) in map {
                if PRIVATE_KEYS.contains(&pystr::casefold(key).as_str()) {
                    return Err(value_error(format!("{path} contains private credential material")));
                }
                scan_private(item, &format!("{path}.{key}"))?;
            }
        }
        Json::Array(items) => {
            for (index, item) in items.iter().enumerate() {
                scan_private(item, &format!("{path}[{index}]"))?;
            }
        }
        Json::String(text) if absolute_path(text) => {
            return Err(value_error(format!("{path} contains an absolute path")));
        }
        _ => {}
    }
    Ok(())
}

const PLAN_FIELDS: [&str; 23] = [
    "bindings",
    "boundaries",
    "completion_conditions",
    "continuation_conditions",
    "failure_conditions",
    "format_id",
    "format_version",
    "gm_guidance",
    "id",
    "initial_state",
    "intended_player_experience",
    "objectives",
    "premise",
    "revision",
    "secrets",
    "setting_references",
    "side_content",
    "starting_location",
    "starting_party",
    "structure",
    "title",
    "tone",
    "unsupported_mechanics",
];
const ACT_FIELDS: [&str; 10] = [
    "branches",
    "consequences",
    "dynamic_encounter_guidance",
    "id",
    "objectives",
    "planned_encounters",
    "setting_refs",
    "situations",
    "summary",
    "title",
];

fn unique(values: &[String]) -> bool {
    let set: BTreeSet<&String> = values.iter().collect();
    set.len() == values.len()
}

fn ongoing_ending(plan: &JsonMap) -> bool {
    matches!(plan.get("ending"), Some(Json::Object(ending)) if ending.get("mode").is_some_and(|mode| py_eq(mode, &Json::from("ongoing"))))
}

/// `validate_campaign_plan(payload, readable_markdown=..., ...)`: validate one closed plan and
/// return canonical deterministic publication data. With no expected Ruleset and Setting, the
/// plan must be a No Ruleset Plan whose own world entities replace Setting paths.
pub fn validate_campaign_plan(
    payload: &Json,
    readable_markdown: &str,
    expected: CampaignPlanExpectations<'_>,
) -> DomainResult<ValidatedCampaignPlan> {
    let plan = as_object(payload, "Campaign Plan")?;
    exact(plan, &PLAN_FIELDS, "Campaign Plan", &["opening_direction", "ending"], Some("$"))?;
    if !py_eq(&plan["format_id"], &Json::from(CAMPAIGN_PLAN_FORMAT_ID))
        || !py_eq(&plan["format_version"], &Json::from(CAMPAIGN_PLAN_FORMAT_VERSION))
    {
        return Err(value_error("Campaign Plan format is unsupported"));
    }
    let plan_id = identity(&plan["id"], "Campaign Plan identity")?;
    let revision_ok = match &plan["revision"] {
        Json::Number(number) if number.is_u64() => number.as_u64().is_some_and(|r| r >= 1),
        _ => false,
    };
    if !revision_ok {
        return Err(value_error("Campaign Plan revision is invalid"));
    }
    let title = text(&plan["title"], "Campaign Plan title", 160)?;
    text(&plan["premise"], "Campaign Plan premise", 20_000)?;
    text(&plan["tone"], "Campaign Plan tone", 500)?;
    texts(&plan["boundaries"], "Campaign Plan boundaries", 0, 256)?;
    text(&plan["intended_player_experience"], "Campaign Plan intended experience", 20_000)?;
    if let Some(opening) = plan.get("opening_direction") {
        text(opening, "Campaign Plan opening direction", 4_000)?;
    }

    let bindings = as_object(&plan["bindings"], "Campaign Plan bindings")?;
    exact(
        bindings,
        &["campaign_setting", "package_closure_sha256", "ruleset"],
        "Campaign Plan bindings",
        &[],
        Some("$.bindings"),
    )?;
    let no_ruleset_paths;
    let (setting_paths, ruleset_id) =
        if expected.expected_ruleset.is_none() && expected.expected_setting.is_none() {
            if !is_no_ruleset_plan(plan) {
                return Err(value_error("Campaign Plan package bindings do not match the admitted closure"));
            }
            no_ruleset_paths = validate_no_ruleset_world(plan)?;
            (&no_ruleset_paths, String::new())
        } else {
            let ruleset = package_binding(&bindings["ruleset"], "Ruleset binding")?;
            let setting = package_binding(&bindings["campaign_setting"], "Campaign Setting binding")?;
            if Some(&ruleset) != expected.expected_ruleset || Some(&setting) != expected.expected_setting {
                return Err(value_error("Campaign Plan package bindings do not match the admitted closure"));
            }
            (expected.setting_paths, ruleset.package_id)
        };
    let closure = text(&bindings["package_closure_sha256"], "Package closure hash", 64)?;
    if closure != expected.expected_closure_sha256 {
        return Err(value_error("Campaign Plan package bindings do not match the admitted closure"));
    }

    let party = parse_starting_party_policy(&plan["starting_party"])?;
    let mut references: Vec<String> = Vec::new();
    references.extend(party.fixed_player_character.iter().map(|item| item.path.clone()));
    references.extend(party.required_companions.iter().map(|item| item.path.clone()));
    references.push(setting_reference(&plan["starting_location"], "Starting location", None)?);

    let initial_state = as_object(&plan["initial_state"], "Initial state")?;
    exact(
        initial_state,
        &["entities", "facts"],
        "Initial state",
        &["mechanics", "party_placements"],
        Some("$.initial_state"),
    )?;
    if let Some(placements) = initial_state.get("party_placements") {
        validate_party_placements(placements, &ruleset_id)?;
    }
    let empty = Json::Array(Vec::new());
    let Some(mechanics) =
        initial_state.get("mechanics").unwrap_or(&empty).as_array().filter(|items| items.len() <= 256)
    else {
        return Err(value_error("Initial mechanical state is invalid"));
    };
    let mut state_keys: Vec<(&str, &str)> = Vec::new();
    for raw in mechanics {
        let state = as_object(raw, "Initial mechanical state entry")?;
        exact(state, &["namespace", "key", "value"], "Initial mechanical state entry", &[], None)?;
        let (namespace, key, value) = state_value(state)?;
        if !belongs_to(namespace, &ruleset_id) {
            return Err(value_error("Initial mechanical state must belong to the Plan's Ruleset"));
        }
        if !canonical_payload_is_safe(&json!({namespace: {key: value}})) {
            return Err(value_error("Initial mechanical state contains a reserved canonical channel"));
        }
        if state_keys.contains(&(namespace, key)) {
            return Err(value_error("Initial mechanical state keys must be unique"));
        }
        state_keys.push((namespace, key));
    }
    let Some(entities) = initial_state["entities"].as_array().filter(|items| items.len() <= 128) else {
        return Err(value_error("Initial state entities are invalid"));
    };
    let mut entity_ids = Vec::with_capacity(entities.len());
    for (index, raw) in entities.iter().enumerate() {
        let entity_path = format!("$.initial_state.entities[{index}]");
        let entity = as_object(raw, "Initial state entity")?;
        exact(
            entity,
            &["id", "kind", "source_ref", "state"],
            "Initial state entity",
            &[],
            Some(&entity_path),
        )?;
        entity_ids.push(identity(&entity["id"], "Initial state entity identity")?);
        identity(&entity["kind"], "Initial state entity kind")?;
        references.push(setting_reference(
            &entity["source_ref"],
            "Initial state entity source_ref",
            Some(&format!("{entity_path}.source_ref")),
        )?);
        as_object(&entity["state"], "Initial state entity state")?;
    }
    if !unique(&entity_ids) {
        return Err(value_error("Initial state entity identities must be unique"));
    }
    let Some(facts) = initial_state["facts"].as_array().filter(|items| items.len() <= 256) else {
        return Err(value_error("Initial state facts are invalid"));
    };
    let mut fact_ids = Vec::new();
    for raw in facts {
        if raw.is_string() {
            text(raw, "Initial state fact", 20_000)?;
            continue;
        }
        let fact = as_object(raw, "Initial state fact")?;
        exact(fact, &["fact", "id", "known_by"], "Initial state fact", &[], None)?;
        fact_ids.push(identity(&fact["id"], "Initial state fact identity")?);
        text(&fact["fact"], "Initial state fact text", 20_000)?;
        texts(&fact["known_by"], "Initial state fact knowledge", 0, 128)?;
    }
    if !unique(&fact_ids) {
        return Err(value_error("Initial state fact identities must be unique"));
    }

    let reference_groups = as_object(&plan["setting_references"], "Setting references")?;
    let groups = ["creatures", "factions", "items", "locations", "lore", "npcs"];
    exact(reference_groups, &groups, "Setting references", &[], None)?;
    for group in groups {
        let Some(values) = reference_groups[group].as_array() else {
            return Err(value_error(format!("Setting reference group {group} is invalid")));
        };
        for item in values {
            references.push(setting_reference(item, &format!("{group} reference"), None)?);
        }
    }
    if references.len() > CAMPAIGN_PLAN_REFERENCE_LIMIT {
        return Err(value_error("Campaign Plan has too many Setting references"));
    }
    let referenced: BTreeSet<String> = references.into_iter().collect();
    if let Some(missing) = referenced.iter().find(|path| !setting_paths.contains(*path)) {
        return Err(value_error(format!("Campaign Plan references missing Setting content: {missing}")));
    }

    let Some(structure) = plan["structure"].as_array().filter(|items| (1..=64).contains(&items.len())) else {
        return Err(value_error("Campaign Plan structure is invalid"));
    };
    let mut act_ids = Vec::with_capacity(structure.len());
    for raw in structure {
        let act = as_object(raw, "Campaign act")?;
        exact(act, &ACT_FIELDS, "Campaign act", &[], None)?;
        act_ids.push(identity(&act["id"], "Campaign act identity")?);
        text(&act["title"], "Campaign act title", 160)?;
        text(&act["summary"], "Campaign act summary", 20_000)?;
        for field in [
            "branches",
            "consequences",
            "dynamic_encounter_guidance",
            "objectives",
            "planned_encounters",
            "situations",
        ] {
            texts(&act[field], &format!("Campaign act {field}"), 0, 128)?;
        }
        for path in texts(&act["setting_refs"], "Campaign act Setting references", 0, 128)? {
            validate_portable_path(&path)?;
            if !setting_paths.contains(&path) {
                return Err(value_error(format!("Campaign act references missing Setting content: {path}")));
            }
        }
    }
    if !unique(&act_ids) {
        return Err(value_error("Campaign act identities must be unique"));
    }

    let Some(secrets) = plan["secrets"].as_array().filter(|items| items.len() <= 256) else {
        return Err(value_error("Campaign Plan secrets are invalid"));
    };
    let mut secret_ids = Vec::new();
    for raw in secrets {
        if raw.is_string() {
            text(raw, "Campaign Plan secret", 20_000)?;
            continue;
        }
        let secret = as_object(raw, "Campaign Plan secret")?;
        let has =
            |fields: &[&str]| secret.len() == fields.len() && fields.iter().all(|f| secret.contains_key(*f));
        let secret_id = if has(&["fact", "id", "known_by", "reveal"]) {
            let secret_id = identity(&secret["id"], "Campaign Plan secret identity")?;
            if expected.setting_knowledge_ids.contains(&secret_id) {
                return Err(value_error("Existing Campaign Setting secrets must use setting_fact_id"));
            }
            text(&secret["fact"], "Campaign Plan secret fact", 20_000)?;
            texts(&secret["known_by"], "Campaign Plan secret knowledge", 0, 128)?;
            secret_id
        } else if has(&["id", "setting_fact_id", "reveal"]) {
            let secret_id = identity(&secret["id"], "Campaign Plan secret identity")?;
            let setting_fact_id =
                identity(&secret["setting_fact_id"], "Campaign Setting knowledge fact identity")?;
            if secret_id != setting_fact_id {
                return Err(value_error("Campaign Plan Setting secret identities must match"));
            }
            if !expected.setting_knowledge_ids.contains(&setting_fact_id) {
                return Err(value_error("Campaign Plan references missing Campaign Setting knowledge"));
            }
            secret_id
        } else {
            return Err(value_error("Campaign Plan secret fields do not match the portable format"));
        };
        secret_ids.push(secret_id);
        let reveal = as_object(&secret["reveal"], "Campaign Plan secret reveal")?;
        exact(reveal, &["condition", "from_act"], "Campaign Plan secret reveal", &[], None)?;
        text(&reveal["condition"], "Campaign Plan secret reveal condition", 20_000)?;
        let from_act = identity(&reveal["from_act"], "Campaign Plan secret reveal act")?;
        if !act_ids.contains(&from_act) {
            return Err(value_error("Campaign Plan secret reveal act is not declared"));
        }
    }
    if !unique(&secret_ids) {
        return Err(value_error("Campaign Plan secret identities must be unique"));
    }

    let ongoing = ongoing_ending(plan);
    let Some(objectives) = plan["objectives"].as_array().filter(|items| !items.is_empty() || ongoing) else {
        return Err(value_error("Campaign Plan objectives are invalid"));
    };
    let mut objective_ids = Vec::with_capacity(objectives.len());
    for raw in objectives {
        let objective = as_object(raw, "Campaign objective")?;
        exact(
            objective,
            &["completion_conditions", "description", "id", "title"],
            "Campaign objective",
            &[],
            None,
        )?;
        objective_ids.push(identity(&objective["id"], "Campaign objective identity")?);
        text(&objective["title"], "Campaign objective title", 160)?;
        text(&objective["description"], "Campaign objective description", 20_000)?;
        texts(&objective["completion_conditions"], "Campaign objective conditions", 1, 64)?;
    }
    if !unique(&objective_ids) {
        return Err(value_error("Campaign objective identities must be unique"));
    }
    for (index, raw) in structure.iter().enumerate() {
        let act = as_object(raw, "Campaign act")?;
        let act_id = act["id"].as_str().unwrap_or_default();
        // `raw is structure[-1]`: JSON gives each act its own object, so identity is position.
        let ongoing_final = ongoing && index + 1 == structure.len();
        let required = texts(
            &act["objectives"],
            &format!("Campaign act {act_id} objectives"),
            if ongoing_final { 0 } else { 1 },
            128,
        )?;
        if !unique(&required) {
            return Err(value_error(format!("Campaign act {act_id} repeats an objective reference")));
        }
        if let Some(missing) = required.iter().find(|id| !objective_ids.contains(id)) {
            return Err(value_error(format!("Campaign act {act_id} references missing objective {missing}")));
        }
    }

    let guidance = as_object(&plan["gm_guidance"], "GM guidance")?;
    exact(
        guidance,
        &["instructions", "player_freedom", "preparation_detail", "railroading"],
        "GM guidance",
        &[],
        None,
    )?;
    let mut fields: Vec<&String> = guidance.keys().collect();
    fields.sort_unstable();
    for field in fields {
        text(&guidance[field.as_str()], &format!("GM guidance {field}"), 20_000)?;
    }
    for field in [
        "completion_conditions",
        "continuation_conditions",
        "failure_conditions",
        "side_content",
        "unsupported_mechanics",
    ] {
        texts(&plan[field], &format!("Campaign Plan {field}"), 0, 256)?;
    }

    scan_private(payload, "plan")?;
    let markdown = text(
        &Json::from(pystr::strip(readable_markdown)),
        "Campaign Plan readable rendering",
        CAMPAIGN_PLAN_BYTE_LIMIT,
    )?;
    if markdown.len() > CAMPAIGN_PLAN_BYTE_LIMIT {
        return Err(value_error("Campaign Plan readable rendering is too large"));
    }
    validate_content_shape("campaign_plan", payload, Some("plan.json"))?;
    validate_ending(plan)?;
    let captured = plan.clone();
    let encoded = canonical_json_bytes(payload);
    if encoded.len() > CAMPAIGN_PLAN_BYTE_LIMIT {
        return Err(value_error("Campaign Plan is too large"));
    }
    let content_sha256 = sha256_hex(&encoded);
    Ok(ValidatedCampaignPlan {
        artifact_id: format!("campaign-plan-{plan_id}-{}", &content_sha256[..16]),
        payload: captured,
        content_sha256,
        title,
        readable_markdown: markdown,
        referenced_setting_paths: referenced.into_iter().collect(),
    })
}
