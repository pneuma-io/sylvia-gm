//! Package content declarations, gameplay support claims and Campaign Setting knowledge.

use super::manifest::PackageManifest;
use super::record::{bounded_array, exact_keys, object, sorted_unique, sorted_unique_identities};
use super::{parse_canonical_json_bytes, validate_portable_path_value, JsonShapeLimits, DEFAULT_LABEL};
use crate::authority::validate_authority_identity_value;
use crate::content::validate_content_shape;
use crate::error::{DomainError, DomainResult};
use crate::pyops::{py_eq, py_hashable, py_unhashable};
use std::collections::BTreeSet;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{pystr, Json};

pub const CONTENT_DECLARATION_BYTE_LIMIT: usize = 32 * 1024 * 1024;
pub const CONTENT_DECLARATION_DEPTH_LIMIT: usize = 16;
pub const CONTENT_DECLARATION_NODE_LIMIT: usize = 250_000;
pub const CONTENT_DECLARATION_OBJECT_MEMBER_LIMIT: usize = 32;
pub const CONTENT_DECLARATION_SOURCE_AUTHORITY_LIMIT: usize = 64;
pub const CONTENT_DECLARATION_BOUNDARY_REFERENCE_LIMIT: usize = 256;
pub const CONTENT_DECLARATION_RECORD_LIMIT: usize = 10_000;
pub const CONTENT_DECLARATION_SOURCE_LOCATOR_LIMIT: usize = 64;
pub const CONTENT_DECLARATION_SECTION_PATH_LIMIT: usize = 32;
pub const CONTENT_DECLARATION_LINK_FAMILY_LIMIT: usize = 256;
pub const CONTENT_DECLARATION_EVIDENCE_LIMIT: usize = 256;
pub const CONTENT_DECLARATION_CLASSIFICATION_LIMIT: usize = 8;
pub const CAMPAIGN_SETTING_KNOWLEDGE_PATH: &str = "setting-knowledge.json";
pub const CAMPAIGN_SETTING_KNOWLEDGE_BYTE_LIMIT: usize = 256 * 1024;
pub const CAMPAIGN_SETTING_KNOWLEDGE_FACT_LIMIT: usize = 128;
pub const GAMEPLAY_SUPPORT_PATH: &str = "support/gameplay-resolution.json";
pub const GAMEPLAY_SUPPORT_BYTE_LIMIT: usize = 2 * 1024 * 1024;
pub const GAMEPLAY_SUPPORT_RECORD_LIMIT: usize = 10_000;

/// One source document a declaration cites (`ContentSourceAuthority`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ContentSourceAuthority {
    pub identity: String,
    pub document_id: String,
    pub version: String,
    pub media_type: String,
    pub content_sha256: String,
    pub license_id: String,
}

/// One located source unit (`ContentSourceLocator`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ContentSourceLocator {
    pub authority_id: String,
    pub source_unit_id: String,
    pub kind: String,
    pub page: Option<i64>,
    pub section_path: Vec<String>,
    pub derived_text_sha256: Option<String>,
    pub page_range: Option<(i64, i64)>,
    pub content_sha256: Option<String>,
}

/// The local surfaces one inclusion links (`ContentLinks`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ContentLinks {
    pub artifacts: Vec<String>,
    pub schemas: Vec<String>,
    pub state_namespaces: Vec<String>,
    pub mechanics: Vec<String>,
    pub procedures: Vec<String>,
    pub capabilities: Vec<String>,
    pub tools: Vec<String>,
    pub prompt_fragments: Vec<String>,
    pub ui_surfaces: Vec<String>,
}

/// One included content record (`ContentInclusion`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ContentInclusion {
    pub identity: String,
    pub kind: String,
    pub family: String,
    pub disposition: String,
    pub classifications: Vec<String>,
    pub sources: Vec<ContentSourceLocator>,
    pub links: ContentLinks,
    pub test_evidence: Vec<String>,
}

/// One excluded content record (`ContentExclusion`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ContentExclusion {
    pub identity: String,
    pub kind: String,
    pub family: String,
    pub reason: String,
    pub classifications: Vec<String>,
    pub sources: Vec<ContentSourceLocator>,
    pub within_declared_scope: bool,
}

/// A parsed content-declaration artifact (`PackageContentDeclarations`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PackageContentDeclarations {
    pub scope_id: String,
    pub scope_kind: String,
    pub claim_status: String,
    pub source_boundary_refs: Vec<(String, String)>,
    pub source_authorities: Vec<ContentSourceAuthority>,
    pub inclusions: Vec<ContentInclusion>,
    pub exclusions: Vec<ContentExclusion>,
    pub canonical_json: String,
}

/// One installed content claim (`GameplaySupportRecord`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GameplaySupportRecord {
    pub identity: String,
    pub reference_availability: String,
    pub gameplay_resolution: String,
    pub mechanic_family: Option<String>,
    pub deterministic_part: Option<String>,
    pub gm_part: Option<String>,
}

/// Validated installed support claims for one gameplay package (`GameplaySupport`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GameplaySupport {
    pub records: Vec<GameplaySupportRecord>,
    pub canonical_json: String,
}

/// One fact a Campaign Setting gives the GM (`CampaignSettingKnowledgeFact`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CampaignSettingKnowledgeFact {
    pub identity: String,
    pub fact: String,
    pub known_by: Vec<String>,
    pub source_path: String,
}

/// A Campaign Setting's GM knowledge (`CampaignSettingKnowledge`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CampaignSettingKnowledge {
    pub facts: Vec<CampaignSettingKnowledgeFact>,
    pub private_references: Vec<String>,
    pub canonical_json: String,
}

fn value(message: &str) -> DomainError {
    DomainError::value(message)
}

fn identity(value: &Json, label: &str) -> DomainResult<String> {
    Ok(validate_authority_identity_value(value, label, false)?.to_owned())
}

fn sha(value: &Json, label: &str) -> DomainResult<String> {
    Ok(validate_authority_identity_value(value, label, true)?.to_owned())
}

/// Python `value in a_set_of_strings`: `TypeError` for an unhashable value.
fn member(value: &Json, names: &[&str]) -> DomainResult<bool> {
    if !py_hashable(value) {
        return Err(py_unhashable(value));
    }
    Ok(value.as_str().is_some_and(|text| names.contains(&text)))
}

/// A Python `int` that is not a `bool`.
fn int_value(value: &Json) -> Option<i128> {
    match value {
        Json::Number(number) if number.is_i64() => number.as_i64().map(i128::from),
        Json::Number(number) if number.is_u64() => number.as_u64().map(i128::from),
        _ => None,
    }
}

/// `_content_sources(value, authorities=..., label=...)`.
fn content_sources(
    value: &Json,
    authorities: &BTreeSet<String>,
    label: &str,
) -> DomainResult<Vec<ContentSourceLocator>> {
    let mut result = Vec::new();
    for raw in bounded_array(value, label, CONTENT_DECLARATION_SOURCE_LOCATOR_LIMIT)? {
        let source = object(raw, label)?;
        exact_keys(source, &["authority_id", "source_unit_id", "locator"], label)?;
        let authority_id = identity(&source["authority_id"], &format!("{label} authority"))?;
        if !authorities.contains(&authority_id) {
            return Err(DomainError::value(format!("{label} references an unknown source authority")));
        }
        let source_unit_id = identity(&source["source_unit_id"], &format!("{label} unit"))?;
        let locator_label = format!("{label} locator");
        let locator = object(&source["locator"], &locator_label)?;
        let kind = locator.get("kind").cloned().unwrap_or(Json::Null);
        if py_eq(&kind, &Json::from("pdf_page_section")) {
            exact_keys(locator, &["kind", "page", "section_path", "derived_text_sha256"], &locator_label)?;
            let page = int_value(&locator["page"]).filter(|page| (1..=100_000).contains(page));
            let Some(page) = page else {
                return Err(DomainError::value(format!("{label} locator page is invalid")));
            };
            let section_path = bounded_array(
                &locator["section_path"],
                &format!("{label} section path"),
                CONTENT_DECLARATION_SECTION_PATH_LIMIT,
            )?;
            let section_ok = !section_path.is_empty()
                && section_path.iter().all(|item| {
                    item.as_str().is_some_and(|text| {
                        !text.is_empty() && text == pystr::strip(text) && text.len() <= 256
                    })
                });
            if !section_ok {
                return Err(DomainError::value(format!("{label} section path is invalid")));
            }
            let derived = sha(&locator["derived_text_sha256"], &format!("{label} derived text SHA-256"))?;
            result.push(ContentSourceLocator {
                authority_id,
                source_unit_id,
                kind: "pdf_page_section".into(),
                page: Some(page as i64),
                section_path: section_path.iter().filter_map(Json::as_str).map(str::to_owned).collect(),
                derived_text_sha256: Some(derived),
                page_range: None,
                content_sha256: None,
            });
        } else if py_eq(&kind, &Json::from("pdf_page_range")) {
            exact_keys(locator, &["kind", "pages", "content_sha256"], &locator_label)?;
            let pages = bounded_array(&locator["pages"], &format!("{label} page range"), 2)?;
            let numbers: Vec<Option<i128>> = pages.iter().map(int_value).collect();
            let (Some(Some(first)), Some(Some(last)), 2) = (numbers.first(), numbers.get(1), pages.len())
            else {
                return Err(DomainError::value(format!("{label} locator page range is invalid")));
            };
            if !(1 <= *first && first <= last && *last <= 100_000) {
                return Err(DomainError::value(format!("{label} locator page range is invalid")));
            }
            let content = sha(&locator["content_sha256"], &format!("{label} content SHA-256"))?;
            result.push(ContentSourceLocator {
                authority_id,
                source_unit_id,
                kind: "pdf_page_range".into(),
                page: None,
                section_path: Vec::new(),
                derived_text_sha256: None,
                page_range: Some((*first as i64, *last as i64)),
                content_sha256: Some(content),
            });
        } else {
            return Err(DomainError::value(format!("{label} locator kind is unsupported")));
        }
    }
    let identities: Vec<(&str, &str)> =
        result.iter().map(|item| (item.authority_id.as_str(), item.source_unit_id.as_str())).collect();
    if !sorted_unique(&identities) {
        return Err(DomainError::value(format!("{label} must be sorted and unique")));
    }
    Ok(result)
}

const LINK_FIELDS: &[&str] = &[
    "artifacts",
    "schemas",
    "state_namespaces",
    "mechanics",
    "procedures",
    "capabilities",
    "tools",
    "prompt_fragments",
    "ui_surfaces",
];

/// `_content_links(value)`.
fn content_links(value: &Json) -> DomainResult<ContentLinks> {
    let links = object(value, "Content inclusion links")?;
    exact_keys(links, LINK_FIELDS, "Content inclusion links")?;
    let mut values = Vec::with_capacity(LINK_FIELDS.len());
    for field in LINK_FIELDS {
        values.push(sorted_unique_identities(
            &links[*field],
            &format!("Content {field}"),
            CONTENT_DECLARATION_LINK_FAMILY_LIMIT,
        )?);
    }
    let mut values = values.into_iter();
    let mut next = || values.next().expect("one list per field");
    Ok(ContentLinks {
        artifacts: next(),
        schemas: next(),
        state_namespaces: next(),
        mechanics: next(),
        procedures: next(),
        capabilities: next(),
        tools: next(),
        prompt_fragments: next(),
        ui_surfaces: next(),
    })
}

/// `parse_campaign_setting_knowledge_bytes(value)`.
pub fn parse_campaign_setting_knowledge_bytes(bytes: &[u8]) -> DomainResult<CampaignSettingKnowledge> {
    if bytes.len() > CAMPAIGN_SETTING_KNOWLEDGE_BYTE_LIMIT {
        return Err(value("Campaign Setting knowledge is too large"));
    }
    let limits = JsonShapeLimits::new(8, 10_000).with_object_members(8);
    let parsed_json = parse_canonical_json_bytes(bytes, "Campaign Setting knowledge", limits)?;
    let parsed = object(&parsed_json, "Campaign Setting knowledge")?;
    validate_content_shape(
        "campaign_setting_knowledge",
        &parsed_json,
        Some(CAMPAIGN_SETTING_KNOWLEDGE_PATH),
    )?;
    exact_keys(
        parsed,
        &["format_id", "format_version", "facts", "private_references"],
        "Campaign Setting knowledge",
    )?;
    if !py_eq(&parsed["format_id"], &Json::from("sylvia-gm-campaign-setting-knowledge"))
        || !py_eq(&parsed["format_version"], &Json::from(1))
    {
        return Err(value("Campaign Setting knowledge format is unsupported"));
    }
    let mut private_references = Vec::new();
    for item in bounded_array(&parsed["private_references"], "Campaign Setting private references", 256)? {
        private_references.push(validate_portable_path_value(item)?.to_owned());
    }
    if !sorted_unique(&private_references) {
        return Err(value("Campaign Setting private references must be sorted and unique"));
    }
    let mut facts = Vec::new();
    for raw in bounded_array(
        &parsed["facts"],
        "Campaign Setting knowledge facts",
        CAMPAIGN_SETTING_KNOWLEDGE_FACT_LIMIT,
    )? {
        let fact = object(raw, "Campaign Setting knowledge fact")?;
        exact_keys(fact, &["id", "fact", "known_by", "source_path"], "Campaign Setting knowledge fact")?;
        let fact_identity = identity(&fact["id"], "Campaign Setting knowledge fact identity")?;
        let text = match &fact["fact"] {
            Json::String(text) if !text.is_empty() && text == pystr::strip(text) && text.len() <= 4_000 => {
                text
            }
            _ => return Err(value("Campaign Setting knowledge fact text is invalid")),
        };
        let known_by =
            sorted_unique_identities(&fact["known_by"], "Campaign Setting knowledge fact identities", 128)?;
        facts.push(CampaignSettingKnowledgeFact {
            identity: fact_identity,
            fact: text.clone(),
            known_by,
            source_path: validate_portable_path_value(&fact["source_path"])?.to_owned(),
        });
    }
    let identities: Vec<&str> = facts.iter().map(|item| item.identity.as_str()).collect();
    if !sorted_unique(&identities) {
        return Err(value("Campaign Setting knowledge facts must be sorted and unique"));
    }
    Ok(CampaignSettingKnowledge {
        facts,
        private_references,
        canonical_json: String::from_utf8(bytes.to_vec()).expect("strict JSON is UTF-8"),
    })
}

/// `validate_campaign_setting_knowledge_links(manifest, knowledge)`.
pub fn validate_campaign_setting_knowledge_links(
    manifest: &PackageManifest,
    knowledge: &CampaignSettingKnowledge,
) -> DomainResult<()> {
    let files: BTreeSet<&str> = manifest.files.iter().map(|file| file.path()).collect();
    let referenced = knowledge
        .private_references
        .iter()
        .map(String::as_str)
        .chain(knowledge.facts.iter().map(|fact| fact.source_path.as_str()));
    if referenced.into_iter().any(|path| !files.contains(path)) {
        return Err(value("Campaign Setting knowledge references missing package content"));
    }
    Ok(())
}

const RESOLUTIONS: &[&str] =
    &["not_applicable", "engine_family", "engine_specialized", "hybrid", "gm_adjudicated", "unsupported"];

/// `parse_gameplay_support_bytes(value)`.
pub fn parse_gameplay_support_bytes(bytes: &[u8]) -> DomainResult<GameplaySupport> {
    if bytes.len() > GAMEPLAY_SUPPORT_BYTE_LIMIT {
        return Err(value("Gameplay support declarations are too large"));
    }
    // Python calls `parse_canonical_json_bytes(value)` with its default label.
    let parsed_json = parse_canonical_json_bytes(bytes, DEFAULT_LABEL, JsonShapeLimits::NONE)?;
    let parsed = object(&parsed_json, "Gameplay support declarations")?;
    validate_content_shape("gameplay_support", &parsed_json, Some(GAMEPLAY_SUPPORT_PATH))?;
    exact_keys(parsed, &["format_id", "format_version", "records"], "Gameplay support declarations")?;
    if !py_eq(&parsed["format_id"], &Json::from("sylvia-gm-gameplay-support"))
        || !py_eq(&parsed["format_version"], &Json::from(1))
    {
        return Err(value("Gameplay support declaration format is unsupported"));
    }
    let mut records = Vec::new();
    for raw in bounded_array(&parsed["records"], "Gameplay support records", GAMEPLAY_SUPPORT_RECORD_LIMIT)? {
        let item = object(raw, "Gameplay support record")?;
        exact_keys(
            item,
            &[
                "id",
                "reference_availability",
                "gameplay_resolution",
                "mechanic_family",
                "deterministic_part",
                "gm_part",
            ],
            "Gameplay support record",
        )?;
        let record_identity = identity(&item["id"], "Gameplay support identity")?;
        let reference = &item["reference_availability"];
        let resolution = &item["gameplay_resolution"];
        if !member(reference, &["included", "excluded"])? {
            return Err(value("Reference Availability is unsupported"));
        }
        if !member(resolution, RESOLUTIONS)? {
            return Err(value("Gameplay Resolution is unsupported"));
        }
        let family = &item["mechanic_family"];
        let deterministic_part = &item["deterministic_part"];
        let gm_part = &item["gm_part"];
        for (label, optional) in
            [("mechanic family", family), ("deterministic part", deterministic_part), ("GM part", gm_part)]
        {
            let valid = match optional {
                Json::Null => true,
                Json::String(text) => !pystr::strip(text).is_empty(),
                _ => false,
            };
            if !valid {
                return Err(DomainError::value(format!("Gameplay support {label} is invalid")));
            }
        }
        if !family.is_null() {
            validate_authority_identity_value(family, "Gameplay mechanic family", false)?;
        }
        let resolution = resolution.as_str().expect("a listed resolution is text");
        let reference = reference.as_str().expect("a listed availability is text");
        if ["engine_family", "hybrid", "gm_adjudicated"].contains(&resolution) && family.is_null() {
            return Err(value("Gameplay Resolution requires a mechanic family"));
        }
        if resolution == "hybrid" && (deterministic_part.is_null() || gm_part.is_null()) {
            return Err(value("Hybrid Gameplay Resolution must identify both owned portions"));
        }
        if resolution != "hybrid" && (!deterministic_part.is_null() || !gm_part.is_null()) {
            return Err(value("Only hybrid Gameplay Resolution declares split ownership"));
        }
        if reference == "excluded"
            && ["engine_family", "engine_specialized", "hybrid", "gm_adjudicated"].contains(&resolution)
        {
            return Err(value("Excluded reference content cannot claim supported resolution"));
        }
        let text = |value: &Json| value.as_str().map(str::to_owned);
        records.push(GameplaySupportRecord {
            identity: record_identity,
            reference_availability: reference.to_owned(),
            gameplay_resolution: resolution.to_owned(),
            mechanic_family: text(family),
            deterministic_part: text(deterministic_part),
            gm_part: text(gm_part),
        });
    }
    let identities: Vec<&str> = records.iter().map(|item| item.identity.as_str()).collect();
    if !sorted_unique(&identities) {
        return Err(value("Gameplay support records must be sorted and unique"));
    }
    Ok(GameplaySupport {
        records,
        canonical_json: String::from_utf8(bytes.to_vec()).expect("strict JSON is UTF-8"),
    })
}

const INCLUSION_CLASSIFICATIONS: &[&str] = &[
    "authored_content",
    "official_licensed_content",
    "mechanically_derived_official",
    "sylvia_gm_implementation",
    "compatibility",
    "interpretation",
];
const EXCLUSION_CLASSIFICATIONS: &[&str] = &[
    "compatibility",
    "interpretation",
    "legal_nonmechanical",
    "official_licensed_content",
    "unavailable_closed_content",
    "unsupported",
];

/// `parse_content_declarations_bytes(value)`: one bounded canonical declaration artifact.
pub fn parse_content_declarations_bytes(bytes: &[u8]) -> DomainResult<PackageContentDeclarations> {
    if bytes.len() > CONTENT_DECLARATION_BYTE_LIMIT {
        return Err(value("Package content declarations are too large"));
    }
    let limits = JsonShapeLimits::new(CONTENT_DECLARATION_DEPTH_LIMIT, CONTENT_DECLARATION_NODE_LIMIT)
        .with_object_members(CONTENT_DECLARATION_OBJECT_MEMBER_LIMIT);
    let parsed_json = parse_canonical_json_bytes(bytes, "Package content declarations", limits)?;
    let parsed = object(&parsed_json, "Package content declarations")?;
    exact_keys(
        parsed,
        &["format_id", "format_version", "scope", "source_authorities", "inclusions", "exclusions"],
        "Package content declarations",
    )?;
    if !py_eq(&parsed["format_id"], &Json::from("sylvia-gm-package-content-declarations"))
        || !py_eq(&parsed["format_version"], &Json::from(1))
    {
        return Err(value("Package content declaration format is unsupported"));
    }
    let inclusion_values =
        bounded_array(&parsed["inclusions"], "Content inclusions", CONTENT_DECLARATION_RECORD_LIMIT)?;
    let exclusion_values =
        bounded_array(&parsed["exclusions"], "Content exclusions", CONTENT_DECLARATION_RECORD_LIMIT)?;
    let combined_count = inclusion_values.len() + exclusion_values.len();
    if combined_count > CONTENT_DECLARATION_RECORD_LIMIT {
        return Err(value("Package content declaration record count is too large"));
    }
    let scope = object(&parsed["scope"], "Content declaration scope")?;
    exact_keys(
        scope,
        &["scope_id", "scope_kind", "claim_status", "source_boundary_refs"],
        "Content declaration scope",
    )?;
    let scope_id = identity(&scope["scope_id"], "Content scope identity")?;
    let scope_kind = identity(&scope["scope_kind"], "Content scope kind")?;
    if !["development_tracer", "bounded_package", "complete_ruleset"].contains(&scope_kind.as_str()) {
        return Err(value("Content scope kind is unsupported"));
    }
    let claim_status = identity(&scope["claim_status"], "Content claim status")?;
    if !["NOT_COMPLETE", "COMPLETE"].contains(&claim_status.as_str()) {
        return Err(value("Content claim status is unsupported"));
    }
    let mut boundary_refs = Vec::new();
    for raw in bounded_array(
        &scope["source_boundary_refs"],
        "Content source boundary references",
        CONTENT_DECLARATION_BOUNDARY_REFERENCE_LIMIT,
    )? {
        let reference = object(raw, "Content source boundary reference")?;
        exact_keys(reference, &["artifact_id", "sha256"], "Content source boundary reference")?;
        boundary_refs.push((
            identity(&reference["artifact_id"], "Content source boundary identity")?,
            sha(&reference["sha256"], "Content source boundary SHA-256")?,
        ));
    }
    if !sorted_unique(&boundary_refs) {
        return Err(value("Content source boundary references must be sorted and unique"));
    }
    let mut authorities = Vec::new();
    for raw in bounded_array(
        &parsed["source_authorities"],
        "Content source authorities",
        CONTENT_DECLARATION_SOURCE_AUTHORITY_LIMIT,
    )? {
        let authority = object(raw, "Content source authority")?;
        exact_keys(
            authority,
            &["id", "document_id", "version", "media_type", "content_sha256", "license_id"],
            "Content source authority",
        )?;
        authorities.push(ContentSourceAuthority {
            identity: identity(&authority["id"], "Content source identity")?,
            document_id: identity(&authority["document_id"], "Content source document")?,
            version: identity(&authority["version"], "Content source version")?,
            media_type: identity(&authority["media_type"], "Content source media type")?,
            content_sha256: sha(&authority["content_sha256"], "Content source SHA-256")?,
            license_id: identity(&authority["license_id"], "Content source license")?,
        });
    }
    let authority_ids: Vec<&str> = authorities.iter().map(|item| item.identity.as_str()).collect();
    if !sorted_unique(&authority_ids) || authority_ids.is_empty() {
        return Err(value("Content source authorities must be sorted, unique, and nonempty"));
    }
    let authority_set: BTreeSet<String> = authority_ids.iter().map(|id| (*id).to_owned()).collect();

    let mut inclusions = Vec::new();
    for raw in inclusion_values {
        let inclusion = object(raw, "Content inclusion")?;
        exact_keys(
            inclusion,
            &["id", "kind", "family", "disposition", "classifications", "sources", "links", "evidence"],
            "Content inclusion",
        )?;
        let disposition = identity(&inclusion["disposition"], "Content inclusion disposition")?;
        if !["included_mechanical", "included_nonmechanical", "included_compatibility"]
            .contains(&disposition.as_str())
        {
            return Err(value("Content inclusion disposition is unsupported"));
        }
        let classifications = sorted_unique_identities(
            &inclusion["classifications"],
            "Content classifications",
            CONTENT_DECLARATION_CLASSIFICATION_LIMIT,
        )?;
        if classifications.is_empty()
            || classifications.iter().any(|c| !INCLUSION_CLASSIFICATIONS.contains(&c.as_str()))
        {
            return Err(value("Content inclusion classifications are unsupported"));
        }
        let sources = content_sources(&inclusion["sources"], &authority_set, "Content inclusion source")?;
        let source_derived = classifications.iter().any(|c| {
            ["compatibility", "interpretation", "mechanically_derived_official", "official_licensed_content"]
                .contains(&c.as_str())
        });
        if source_derived && sources.is_empty() {
            return Err(value("Source-derived content inclusion lacks a source locator"));
        }
        let evidence = object(&inclusion["evidence"], "Content inclusion evidence")?;
        exact_keys(evidence, &["tests"], "Content inclusion evidence")?;
        let inclusion_identity = identity(&inclusion["id"], "Content inclusion identity")?;
        let kind = identity(&inclusion["kind"], "Content inclusion kind")?;
        let family = identity(&inclusion["family"], "Content inclusion family")?;
        let links = content_links(&inclusion["links"])?;
        let test_evidence = sorted_unique_identities(
            &evidence["tests"],
            "Content test evidence",
            CONTENT_DECLARATION_EVIDENCE_LIMIT,
        )?;
        inclusions.push(ContentInclusion {
            identity: inclusion_identity,
            kind,
            family,
            disposition,
            classifications,
            sources,
            links,
            test_evidence,
        });
    }

    let mut exclusions = Vec::new();
    for raw in exclusion_values {
        let exclusion = object(raw, "Content exclusion")?;
        exact_keys(
            exclusion,
            &["id", "kind", "family", "reason", "classifications", "sources", "within_declared_scope"],
            "Content exclusion",
        )?;
        let reason = identity(&exclusion["reason"], "Content exclusion reason")?;
        if !["unavailable", "unsupported", "intentionally_excluded", "superseded", "other_classified"]
            .contains(&reason.as_str())
        {
            return Err(value("Content exclusion reason is unsupported"));
        }
        if exclusion["within_declared_scope"] != Json::Bool(true) {
            return Err(value("Content exclusions must be inside the declared package scope"));
        }
        let classifications = sorted_unique_identities(
            &exclusion["classifications"],
            "Content exclusion classifications",
            CONTENT_DECLARATION_CLASSIFICATION_LIMIT,
        )?;
        if classifications.is_empty()
            || classifications.iter().any(|c| !EXCLUSION_CLASSIFICATIONS.contains(&c.as_str()))
        {
            return Err(value("Content exclusion classifications are unsupported"));
        }
        let sources = content_sources(&exclusion["sources"], &authority_set, "Content exclusion source")?;
        let source_derived = classifications
            .iter()
            .any(|c| ["compatibility", "interpretation", "official_licensed_content"].contains(&c.as_str()));
        if source_derived && sources.is_empty() {
            return Err(value("Source-derived content exclusion lacks a source locator"));
        }
        exclusions.push(ContentExclusion {
            identity: identity(&exclusion["id"], "Content exclusion identity")?,
            kind: identity(&exclusion["kind"], "Content exclusion kind")?,
            family: identity(&exclusion["family"], "Content exclusion family")?,
            reason,
            classifications,
            sources,
            within_declared_scope: true,
        });
    }

    let inclusion_ids: Vec<&str> = inclusions.iter().map(|item| item.identity.as_str()).collect();
    let exclusion_ids: Vec<&str> = exclusions.iter().map(|item| item.identity.as_str()).collect();
    if !sorted_unique(&inclusion_ids) {
        return Err(value("Content inclusions must be sorted and unique"));
    }
    if !sorted_unique(&exclusion_ids) {
        return Err(value("Content exclusions must be sorted and unique"));
    }
    if inclusion_ids.iter().any(|id| exclusion_ids.contains(id)) {
        return Err(value("Content inclusion and exclusion identities collide"));
    }
    if claim_status == "COMPLETE" && scope_kind != "complete_ruleset" {
        return Err(value("Only a complete-ruleset scope can claim completeness"));
    }
    if claim_status == "COMPLETE" && (boundary_refs.is_empty() || combined_count == 0) {
        return Err(value("Complete-ruleset scope requires source boundaries and accounted content"));
    }
    Ok(PackageContentDeclarations {
        scope_id,
        scope_kind,
        claim_status,
        source_boundary_refs: boundary_refs,
        source_authorities: authorities,
        inclusions,
        exclusions,
        canonical_json: String::from_utf8(bytes.to_vec()).expect("strict JSON is UTF-8"),
    })
}

/// `json.dumps(sorted(values)[:8])`.
fn detail(values: &BTreeSet<&str>) -> String {
    let first: Vec<Json> = values.iter().take(8).map(|value| Json::from(*value)).collect();
    pyjson::dumps(&Json::Array(first), Dump::DEFAULT)
}

/// `validate_content_declaration_links(manifest, declarations)`: every format-3 local runtime
/// surface is inclusion-linked exactly.
pub fn validate_content_declaration_links(
    manifest: &PackageManifest,
    declarations: &PackageContentDeclarations,
) -> DomainResult<()> {
    let links: Vec<&ContentLinks> = declarations.inclusions.iter().map(|item| &item.links).collect();
    let collect = |pick: fn(&ContentLinks) -> &Vec<String>| -> BTreeSet<&str> {
        links.iter().flat_map(|link| pick(link).iter().map(String::as_str)).collect()
    };
    let linked_artifacts = collect(|l| &l.artifacts);
    let linked_state_namespaces = collect(|l| &l.state_namespaces);
    let linked_schemas = collect(|l| &l.schemas);
    let linked_capabilities = collect(|l| &l.capabilities);
    let linked_tools = collect(|l| &l.tools);
    let linked_prompts = collect(|l| &l.prompt_fragments);
    let linked_ui = collect(|l| &l.ui_surfaces);
    let local_artifacts: BTreeSet<&str> = manifest.files.iter().map(|file| file.path()).collect();
    let local_schemas: BTreeSet<&str> =
        manifest.participant_artifacts.iter().map(|a| a.schema_id.as_str()).collect();
    let local_capabilities: BTreeSet<&str> = manifest.capabilities.iter().map(String::as_str).collect();
    let local_tools: BTreeSet<&str> = manifest.tools.iter().map(|tool| tool.name.as_str()).collect();
    let local_prompts: BTreeSet<&str> =
        manifest.prompt_fragments.iter().map(|s| s.identity.as_str()).collect();
    let local_ui: BTreeSet<&str> = manifest.ui_surfaces.iter().map(|s| s.identity.as_str()).collect();
    let prefix = format!("{}.", manifest.package_id);
    if linked_state_namespaces.iter().any(|value| !value.starts_with(&prefix)) {
        return Err(value("Content declaration links a foreign package state namespace"));
    }
    for (linked, local, label) in [
        (&linked_artifacts, &local_artifacts, "artifact"),
        (&linked_schemas, &local_schemas, "schema"),
        (&linked_capabilities, &local_capabilities, "capability"),
        (&linked_tools, &local_tools, "tool"),
        (&linked_prompts, &local_prompts, "prompt fragment"),
        (&linked_ui, &local_ui, "UI surface"),
    ] {
        let missing: BTreeSet<&str> = linked.difference(local).copied().collect();
        if !missing.is_empty() {
            return Err(DomainError::value(format!(
                "Content declaration links an unknown local {label}: {}",
                detail(&missing)
            )));
        }
    }
    let mut local_without_declarations = local_artifacts.clone();
    if let Some(path) = &manifest.content_declarations_path {
        local_without_declarations.remove(path.as_str());
    }
    for (local, linked, label) in [
        (&local_without_declarations, &linked_artifacts, "artifact"),
        (&local_schemas, &linked_schemas, "schema"),
        (&local_capabilities, &linked_capabilities, "capability"),
        (&local_tools, &linked_tools, "tool"),
        (&local_prompts, &linked_prompts, "prompt fragment"),
        (&local_ui, &linked_ui, "UI surface"),
    ] {
        let unlinked: BTreeSet<&str> = local.difference(linked).copied().collect();
        if !unlinked.is_empty() {
            return Err(DomainError::value(format!(
                "Package {label} is not linked to a content inclusion: {}",
                detail(&unlinked)
            )));
        }
    }
    Ok(())
}
