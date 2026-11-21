//! `domain/session.py`: rules-neutral Builder and DM session records.
//!
//! Validated records keep their fields private behind `new`, which runs Python's
//! `__post_init__` checks in the same order with the same messages. Plain records keep public
//! fields. Every error is a `ValueError` (or the `TypeError` of `owned_payload`).

use crate::authority::{
    canonical_payload_is_safe, owned_payload, str_enum, validate_authority_identity, WriterRole,
};
use crate::error::{DomainError, DomainResult};
use crate::model_capability::ModelGuidanceDecision;
use serde_json::json;
use sylvia_base::hash::sha256_hex;
use sylvia_base::{pyjson, pystr, Json, JsonMap};

/// `BUILDER_SKILLS`.
pub const BUILDER_SKILLS: &[&str] = &["BUILD RULESET", "BUILD CAMPAIGN SETTING", "BUILD CAMPAIGN PLAN"];

str_enum! {
    /// Lifecycle of one ordinary agent session.
    SessionStatus {
        Active = "active",
        Stopped = "stopped",
        Completed = "completed",
        ReviewRequired = "review_required",
        HandoffClosed = "handoff_closed",
    }
}

str_enum! {
    /// Deterministic Campaign Plan validation result.
    PlanValidationOutcome {
        Valid = "valid",
    }
}

str_enum! {
    /// Only accepted plans become Campaign authority.
    PlanAcceptanceStatus {
        Accepted = "accepted",
    }
}

str_enum! {
    /// Where Builder evidence came from.
    EvidenceSourceKind {
        UserProvided = "user_provided",
        OpenSource = "open_source",
    }
}

str_enum! {
    /// Only safe durable message kinds admitted to session history.
    SessionMessageKind {
        User = "user",
        SafeThink = "safe_think",
        Talk = "talk",
        Steering = "steering",
        Evidence = "evidence",
        CanonicalReference = "canonical_reference",
        HumanReview = "human_review",
    }
}

fn value(message: &str) -> DomainError {
    DomainError::value(message)
}

fn canonical_sha256(value: &Json) -> String {
    sha256_hex(pyjson::canonical_bytes(value))
}

/// The fields of a [`BuilderDMHandoff`], before validation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BuilderDMHandoffFields {
    pub handoff_id: String,
    pub campaign_id: String,
    pub builder_session_id: String,
    pub dm_session_id: String,
    pub package_lock_id: String,
    pub package_candidate_id: Option<String>,
    pub plan_id: String,
    pub plan_revision: i64,
    pub plan_content_sha256: String,
    pub expected_revision: i64,
    pub resulting_campaign_revision: i64,
    pub chronology_ordinal: i64,
    pub completed_turn_id: String,
    pub model_guidance: ModelGuidanceDecision,
    pub payload_sha256: String,
}

/// Stored result of one atomic existing-campaign Builder-to-DM transition (`BuilderDMHandoff`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BuilderDMHandoff {
    fields: BuilderDMHandoffFields,
}

impl BuilderDMHandoff {
    /// `BuilderDMHandoff(...)` with Python's checks, in Python's order.
    pub fn new(fields: BuilderDMHandoffFields) -> DomainResult<Self> {
        for (text, label) in [
            (&fields.handoff_id, "Builder-to-DM handoff identity"),
            (&fields.campaign_id, "Campaign identity"),
            (&fields.builder_session_id, "Builder session identity"),
            (&fields.dm_session_id, "DM session identity"),
            (&fields.package_lock_id, "Handoff package lock identity"),
            (&fields.plan_id, "Handoff Campaign Plan identity"),
            (&fields.completed_turn_id, "Handoff completed TURN identity"),
        ] {
            validate_authority_identity(text, label, false)?;
        }
        if let Some(candidate) = &fields.package_candidate_id {
            validate_authority_identity(candidate, "Handoff package candidate identity", false)?;
        }
        validate_authority_identity(
            &fields.plan_content_sha256,
            "Handoff Campaign Plan content SHA-256",
            true,
        )?;
        validate_authority_identity(&fields.payload_sha256, "Builder-to-DM handoff payload SHA-256", true)?;
        let follows = fields.expected_revision.checked_add(1) == Some(fields.resulting_campaign_revision);
        if fields.plan_revision <= 0
            || fields.expected_revision < 0
            || !follows
            || fields.chronology_ordinal <= 0
        {
            return Err(value("Builder-to-DM handoff revision or chronology is invalid"));
        }
        Ok(Self { fields })
    }

    pub fn fields(&self) -> &BuilderDMHandoffFields {
        &self.fields
    }
}

/// The inputs of `builder_dm_handoff_payload_sha256(...)`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BuilderDMHandoffRequest {
    pub handoff_id: String,
    pub campaign_id: String,
    pub builder_session_id: String,
    pub dm_session_id: String,
    pub dm_provider_id: String,
    pub dm_model_id: String,
    pub expected_writer: WriterRole,
    pub expected_revision: i64,
    pub expected_package_lock_id: String,
    pub expected_package_candidate_id: Option<String>,
    pub plan_id: String,
    pub expected_plan_revision: i64,
    pub expected_plan_content_sha256: String,
    pub completed_turn_id: String,
    pub model_guidance_sha256: String,
}

/// `builder_dm_handoff_payload_sha256(...)`: the stable semantic identity of one handoff request.
pub fn builder_dm_handoff_payload_sha256(request: &BuilderDMHandoffRequest) -> String {
    canonical_sha256(&json!({
        "handoff_id": request.handoff_id,
        "campaign_id": request.campaign_id,
        "builder_session_id": request.builder_session_id,
        "dm_session_id": request.dm_session_id,
        "dm_provider_id": request.dm_provider_id,
        "dm_model_id": request.dm_model_id,
        "expected_writer": request.expected_writer.as_str(),
        "expected_revision": request.expected_revision,
        "expected_package_lock_id": request.expected_package_lock_id,
        "expected_package_candidate_id": request.expected_package_candidate_id,
        "plan_id": request.plan_id,
        "expected_plan_revision": request.expected_plan_revision,
        "expected_plan_content_sha256": request.expected_plan_content_sha256,
        "completed_turn_id": request.completed_turn_id,
        "model_guidance_sha256": request.model_guidance_sha256,
    }))
}

/// The fields of a [`CampaignPlan`], before validation.
#[derive(Debug, Clone, PartialEq)]
pub struct CampaignPlanFields {
    pub plan_id: String,
    pub campaign_id: String,
    pub package_lock_id: String,
    pub source_session_id: String,
    pub content_sha256: String,
    /// Any JSON value; Python requires an object (`owned_payload`).
    pub content: Json,
    pub provenance_evidence_ids: Vec<String>,
    pub validation_outcome: PlanValidationOutcome,
    pub acceptance_status: PlanAcceptanceStatus,
    pub plan_revision: i64,
    pub resulting_campaign_revision: i64,
    pub payload_sha256: String,
}

/// One exact accepted Campaign Plan revision (`CampaignPlan`).
#[derive(Debug, Clone, PartialEq)]
pub struct CampaignPlan {
    fields: CampaignPlanFields,
    content: JsonMap,
}

impl CampaignPlan {
    /// `CampaignPlan(...)` with Python's checks, in Python's order.
    pub fn new(fields: CampaignPlanFields) -> DomainResult<Self> {
        validate_authority_identity(&fields.plan_id, "Campaign Plan identity", false)?;
        validate_authority_identity(&fields.campaign_id, "Campaign identity", false)?;
        validate_authority_identity(&fields.package_lock_id, "Campaign Plan package lock identity", false)?;
        validate_authority_identity(&fields.source_session_id, "Campaign Plan source session", false)?;
        validate_authority_identity(&fields.content_sha256, "Campaign Plan content SHA-256", true)?;
        validate_authority_identity(&fields.payload_sha256, "Campaign Plan payload SHA-256", true)?;
        let content = owned_payload(&fields.content)?;
        let ids = &fields.provenance_evidence_ids;
        if ids.is_empty() || ids.iter().any(String::is_empty) {
            return Err(value("Campaign Plan requires explicit provenance evidence"));
        }
        let mut unique = ids.clone();
        unique.sort_unstable();
        unique.dedup();
        if unique.len() != ids.len() {
            return Err(value("Campaign Plan provenance evidence must be unique"));
        }
        for id in ids {
            validate_authority_identity(id, "Campaign Plan provenance evidence", false)?;
        }
        if fields.plan_revision <= 0 || fields.resulting_campaign_revision <= 0 {
            return Err(value("Accepted Campaign Plan revisions must be positive"));
        }
        Ok(Self { fields, content })
    }

    pub fn fields(&self) -> &CampaignPlanFields {
        &self.fields
    }
    pub fn content(&self) -> &JsonMap {
        &self.content
    }
}

/// The inputs of `campaign_plan_payload_sha256(...)`.
#[derive(Debug, Clone, PartialEq)]
pub struct CampaignPlanRequest {
    pub plan_id: String,
    pub campaign_id: String,
    pub source_session_id: String,
    pub expected_revision: i64,
    pub expected_package_lock_id: String,
    pub content_sha256: String,
    pub content: Json,
    pub provenance_evidence_ids: Vec<String>,
    pub validation_outcome: PlanValidationOutcome,
    pub acceptance_status: PlanAcceptanceStatus,
}

/// `campaign_plan_payload_sha256(...)`: the stable admission identity of one plan request.
pub fn campaign_plan_payload_sha256(request: &CampaignPlanRequest) -> DomainResult<String> {
    let content = owned_payload(&request.content)?;
    Ok(canonical_sha256(&json!({
        "plan_id": request.plan_id,
        "campaign_id": request.campaign_id,
        "source_session_id": request.source_session_id,
        "expected_revision": request.expected_revision,
        "expected_package_lock_id": request.expected_package_lock_id,
        "content_sha256": request.content_sha256,
        "content": Json::Object(content),
        "provenance_evidence_ids": request.provenance_evidence_ids,
        "validation_outcome": request.validation_outcome.as_str(),
        "acceptance_status": request.acceptance_status.as_str(),
    })))
}

/// The fields of a [`SessionRecord`], before validation, with Python's defaults in [`Default`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SessionRecordFields {
    pub session_id: String,
    pub campaign_id: Option<String>,
    pub role: WriterRole,
    pub provider_id: String,
    pub model_id: String,
    pub status: SessionStatus,
    pub parent_session_id: Option<String>,
    pub selected_skill: Option<String>,
    pub scope: Vec<String>,
    pub reasoning_effort: Option<String>,
    pub reasoning_effort_once: Option<String>,
}

impl Default for SessionRecordFields {
    fn default() -> Self {
        Self {
            session_id: String::new(),
            campaign_id: None,
            role: WriterRole::Builder,
            provider_id: String::new(),
            model_id: String::new(),
            status: SessionStatus::Active,
            parent_session_id: None,
            selected_skill: None,
            scope: Vec::new(),
            reasoning_effort: None,
            reasoning_effort_once: None,
        }
    }
}

/// Durable safe metadata for one Builder or DM session (`SessionRecord`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SessionRecord {
    fields: SessionRecordFields,
}

/// A native reasoning setting name: 1 to 32 of `[a-z0-9_-]`, starting with a lowercase letter.
fn native_setting_name(text: &str) -> bool {
    (1..=32).contains(&pystr::len(text))
        && text.chars().next().is_some_and(|c| c.is_ascii_lowercase())
        && text.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_' || c == '-')
}

impl SessionRecord {
    /// `SessionRecord(...)` with Python's checks, in Python's order.
    pub fn new(fields: SessionRecordFields) -> DomainResult<Self> {
        validate_authority_identity(&fields.session_id, "Agent session identity", false)?;
        for preference in [&fields.reasoning_effort, &fields.reasoning_effort_once].into_iter().flatten() {
            if !native_setting_name(preference) {
                return Err(value("Reasoning preference must be a native setting name"));
            }
        }
        if let Some(campaign) = &fields.campaign_id {
            validate_authority_identity(campaign, "Campaign identity", false)?;
        }
        validate_authority_identity(&fields.provider_id, "Provider identity", false)?;
        validate_authority_identity(&fields.model_id, "Model identity", false)?;
        if let Some(parent) = &fields.parent_session_id {
            validate_authority_identity(parent, "parent_session_id", false)?;
            if fields.scope.is_empty() || fields.scope.len() > 8 || fields.scope.iter().any(String::is_empty)
            {
                return Err(value("Child sessions require one to eight explicit scope items"));
            }
        } else if !fields.scope.is_empty() {
            return Err(value("Only child sessions carry isolated scope"));
        }
        if !matches!(fields.role, WriterRole::Builder | WriterRole::Dm) {
            return Err(value("Agent session role must be Builder or DM"));
        }
        if fields.campaign_id.is_none() {
            if fields.role != WriterRole::Builder
                || fields.parent_session_id.is_some()
                || fields.selected_skill.is_some()
                || !fields.scope.is_empty()
            {
                return Err(value("Standalone sessions must be top-level general Builder conversations"));
            }
            return Ok(Self { fields });
        }
        if fields.role == WriterRole::Builder {
            if !fields.selected_skill.as_deref().is_some_and(|skill| BUILDER_SKILLS.contains(&skill)) {
                return Err(value("Builder session must select exactly one construction skill"));
            }
        } else if fields.selected_skill.is_some() {
            return Err(value("DM sessions do not load Builder construction skills"));
        }
        Ok(Self { fields })
    }

    pub fn fields(&self) -> &SessionRecordFields {
        &self.fields
    }
    pub fn session_id(&self) -> &str {
        &self.fields.session_id
    }
    pub fn campaign_id(&self) -> Option<&str> {
        self.fields.campaign_id.as_deref()
    }
    pub fn role(&self) -> WriterRole {
        self.fields.role
    }
}

/// `agent_session_payload_sha256(record)`: every durable session field, bound to one identity.
pub fn agent_session_payload_sha256(record: &SessionRecord) -> String {
    let fields = &record.fields;
    let mut payload = json!({
        "session_id": fields.session_id,
        "campaign_id": fields.campaign_id,
        "role": fields.role.as_str(),
        "provider_id": fields.provider_id,
        "model_id": fields.model_id,
        "status": fields.status.as_str(),
        "parent_session_id": fields.parent_session_id,
        "selected_skill": fields.selected_skill,
        "scope": fields.scope,
    });
    if let Some(effort) = &fields.reasoning_effort {
        payload["reasoning_effort"] = Json::from(effort.as_str());
    }
    if let Some(effort) = &fields.reasoning_effort_once {
        payload["reasoning_effort_once"] = Json::from(effort.as_str());
    }
    canonical_sha256(&payload)
}

/// `len(text) != 64 or any(c not in "0123456789abcdef")`.
fn lowercase_sha(text: &str) -> bool {
    pystr::len(text) == 64 && text.chars().all(|c| "0123456789abcdef".contains(c))
}

/// Safe, bounded evidence; never authoritative campaign truth (`BuilderEvidence`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BuilderEvidence {
    pub evidence_id: String,
    pub source_kind: EvidenceSourceKind,
    pub content_sha256: String,
    pub source_locator: Option<String>,
    pub safe_excerpt: String,
    pub claims: Vec<String>,
}

impl BuilderEvidence {
    /// Python's `__post_init__` checks, in order.
    pub fn validated(self) -> DomainResult<Self> {
        validate_authority_identity(&self.evidence_id, "evidence_id", false)?;
        if !lowercase_sha(&self.content_sha256) {
            return Err(value("content_sha256 must be a lowercase SHA-256 identity"));
        }
        let locator = self.source_locator.as_deref().is_some_and(|text| !text.is_empty());
        if self.source_kind == EvidenceSourceKind::OpenSource && !locator {
            return Err(value("Open-source evidence requires a source locator"));
        }
        if self.source_kind == EvidenceSourceKind::UserProvided && locator {
            return Err(value("User-provided evidence cannot claim an external locator"));
        }
        if self.safe_excerpt.is_empty() || self.claims.is_empty() || self.claims.iter().any(String::is_empty)
        {
            return Err(value("Builder evidence requires a safe excerpt and explicit claims"));
        }
        Ok(self)
    }

    /// `payload()`.
    pub fn payload(&self) -> JsonMap {
        let value = json!({
            "evidence_id": self.evidence_id,
            "source_kind": self.source_kind.as_str(),
            "content_sha256": self.content_sha256,
            "source_locator": self.source_locator,
            "safe_excerpt": self.safe_excerpt,
            "claims": self.claims,
        });
        value.as_object().cloned().expect("an object")
    }
}

/// A structured non-authoritative result from one bounded child (`ChildEvidence`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ChildEvidence {
    pub evidence_id: String,
    pub child_session_id: String,
    pub parent_session_id: String,
    pub content_sha256: String,
    pub safe_summary: String,
    pub claims: Vec<String>,
    /// Python's default is `false`, and `true` is refused.
    pub authoritative: bool,
}

impl ChildEvidence {
    /// Python's `__post_init__` checks, in order.
    pub fn validated(self) -> DomainResult<Self> {
        validate_authority_identity(&self.evidence_id, "evidence_id", false)?;
        validate_authority_identity(&self.child_session_id, "child_session_id", false)?;
        validate_authority_identity(&self.parent_session_id, "parent_session_id", false)?;
        if self.authoritative {
            return Err(value("Child evidence is never authoritative"));
        }
        if !lowercase_sha(&self.content_sha256) {
            return Err(value("content_sha256 must be a lowercase SHA-256 identity"));
        }
        if self.safe_summary.is_empty() || self.claims.is_empty() || self.claims.iter().any(String::is_empty)
        {
            return Err(value("Child evidence requires a safe summary and explicit claims"));
        }
        Ok(self)
    }

    /// `payload()`.
    pub fn payload(&self) -> JsonMap {
        let value = json!({
            "evidence_id": self.evidence_id,
            "child_session_id": self.child_session_id,
            "content_sha256": self.content_sha256,
            "safe_summary": self.safe_summary,
            "claims": self.claims,
            "authoritative": false,
        });
        value.as_object().cloned().expect("an object")
    }
}

/// One durable session history message (`SessionMessage`).
#[derive(Debug, Clone, PartialEq)]
pub struct SessionMessage {
    session_id: String,
    sequence: i64,
    message_id: String,
    kind: SessionMessageKind,
    content: JsonMap,
    canonical_sequence: Option<i64>,
}

impl SessionMessage {
    /// `SessionMessage(...)` with Python's checks, in Python's order.
    pub fn new(
        session_id: impl Into<String>,
        sequence: i64,
        message_id: impl Into<String>,
        kind: SessionMessageKind,
        content: &Json,
        canonical_sequence: Option<i64>,
    ) -> DomainResult<Self> {
        let session_id = session_id.into();
        let message_id = message_id.into();
        validate_authority_identity(&session_id, "Agent session identity", false)?;
        validate_authority_identity(&message_id, "Agent message identity", false)?;
        if sequence <= 0 {
            return Err(value("Agent message sequence must be positive"));
        }
        if !canonical_payload_is_safe(content) {
            return Err(value("Agent message content must be canonical-safe"));
        }
        let content = owned_payload(content)?;
        if (kind == SessionMessageKind::CanonicalReference) != canonical_sequence.is_some() {
            return Err(value("Canonical reference identity is inconsistent"));
        }
        if canonical_sequence.is_some_and(|sequence| sequence <= 0) {
            return Err(value("Canonical reference sequence must be positive"));
        }
        Ok(Self { session_id, sequence, message_id, kind, content, canonical_sequence })
    }

    pub fn session_id(&self) -> &str {
        &self.session_id
    }
    pub fn sequence(&self) -> i64 {
        self.sequence
    }
    pub fn message_id(&self) -> &str {
        &self.message_id
    }
    pub fn kind(&self) -> SessionMessageKind {
        self.kind
    }
    pub fn content(&self) -> &JsonMap {
        &self.content
    }
    pub fn canonical_sequence(&self) -> Option<i64> {
        self.canonical_sequence
    }
}

/// Safe resumable context containing no raw or private reasoning (`SessionContext`).
#[derive(Debug, Clone, PartialEq)]
pub struct SessionContext {
    pub session: SessionRecord,
    pub messages: Vec<SessionMessage>,
}

/// Saved attempt facts for the activity indicator, without chat history (`SessionActivityBasis`).
#[derive(Debug, Clone, PartialEq)]
pub struct SessionActivityBasis {
    pub session: SessionRecord,
    pub accepted: Option<SessionMessage>,
    pub terminal: Option<SessionMessage>,
    pub has_compaction: bool,
}

/// A bounded display page with current form evidence (`SessionHistoryPage`).
#[derive(Debug, Clone, PartialEq)]
pub struct SessionHistoryPage {
    pub session: SessionRecord,
    pub messages: Vec<SessionMessage>,
    pub next_before_sequence: Option<i64>,
    pub latest_sequence: Option<i64>,
    pub latest_user_sequence: Option<i64>,
    pub launch_messages: Vec<SessionMessage>,
    pub latest_canonical_sequence: Option<i64>,
    pub act_markers: Vec<JsonMap>,
    pub campaign_ending: Option<JsonMap>,
    pub campaign_endings: Vec<JsonMap>,
    pub builder_question_messages: Vec<SessionMessage>,
}

/// Durable top-level Builder chat metadata independent of campaigns (`BuilderChatRecord`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BuilderChatRecord {
    session: SessionRecord,
    display_name: String,
    created_ordinal: i64,
}

impl BuilderChatRecord {
    /// `BuilderChatRecord(...)` with Python's checks, in Python's order.
    pub fn new(
        session: SessionRecord,
        display_name: impl Into<String>,
        created_ordinal: i64,
    ) -> DomainResult<Self> {
        let display_name = display_name.into();
        if session.campaign_id().is_some() || session.role() != WriterRole::Builder {
            return Err(value("Builder chat metadata requires a standalone Builder session"));
        }
        if display_name.is_empty() || display_name != pystr::strip(&display_name) {
            return Err(value("Builder chat display name must be trimmed and nonempty"));
        }
        if pystr::len(&display_name) > 120 {
            return Err(value("Builder chat display name is too long"));
        }
        if created_ordinal <= 0 {
            return Err(value("Builder chat creation ordinal must be positive"));
        }
        Ok(Self { session, display_name, created_ordinal })
    }

    pub fn session(&self) -> &SessionRecord {
        &self.session
    }
    pub fn display_name(&self) -> &str {
        &self.display_name
    }
    pub fn created_ordinal(&self) -> i64 {
        self.created_ordinal
    }
}

/// `builder_chat_payload_sha256(record)`.
pub fn builder_chat_payload_sha256(record: &BuilderChatRecord) -> String {
    canonical_sha256(&json!({
        "session": agent_session_payload_sha256(&record.session),
        "display_name": record.display_name,
        "created_ordinal": record.created_ordinal,
    }))
}
