//! `domain/authority.py`: ruleset-neutral campaign authority contracts.
//!
//! Python's frozen dataclasses validate in `__post_init__`. Here the types with validation keep
//! their fields private and are built through `new`, which runs the same checks in the same
//! order with the same messages, so an invalid value cannot exist. Plain records without
//! validation keep public fields.
//!
//! Python's `FrozenJsonDict` and `FrozenJsonList` are immutable JSON trees. A Rust
//! [`Json`] is already owned and only changed through `&mut`, and it can only hold finite
//! numbers and string keys, so `owned_json_value` and `thaw_json_value` have nothing left to
//! check for a [`Json`]; [`owned_payload`] keeps the one check that can fail.

use crate::error::{DomainError, DomainResult};
use std::collections::HashSet;
use sylvia_base::{pystr, Json, JsonMap};

mod records;
pub use records::*;

/// Python `StrEnum`: `as_str` is the value, `parse` is `Enum(value)` without the exception.
macro_rules! str_enum {
    ($(#[$meta:meta])* $name:ident { $($(#[$vmeta:meta])* $variant:ident = $value:literal,)+ }) => {
        $(#[$meta])*
        #[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
        pub enum $name {
            $($(#[$vmeta])* $variant,)+
        }

        impl $name {
            /// Every member in declaration order.
            pub const ALL: &'static [$name] = &[$($name::$variant,)+];

            /// The member's value, as `member.value` gives it.
            pub const fn as_str(self) -> &'static str {
                match self {
                    $($name::$variant => $value,)+
                }
            }

            /// `Enum(value)`; `None` where Python raises `ValueError`.
            pub fn parse(value: &str) -> Option<Self> {
                match value {
                    $($value => Some($name::$variant),)+
                    _ => None,
                }
            }
        }

        impl std::fmt::Display for $name {
            fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                formatter.write_str(self.as_str())
            }
        }
    };
}
pub(crate) use str_enum;

/// `validate_authority_identity(value, label, sha256=...)` for a value already known to be text.
///
/// Whitespace is Python's `str.strip()` set and the SHA-256 length counts code points.
pub fn validate_authority_identity<'a>(value: &'a str, label: &str, sha256: bool) -> DomainResult<&'a str> {
    let stripped = pystr::strip(value);
    if stripped.is_empty() {
        return Err(DomainError::value(format!("{label} must be a non-whitespace string")));
    }
    if value != stripped {
        return Err(DomainError::value(format!("{label} must not have leading or trailing whitespace")));
    }
    if sha256 && (pystr::len(value) != 64 || value.chars().any(|c| !"0123456789abcdef".contains(c))) {
        return Err(DomainError::value(format!("{label} must be 64 lowercase hexadecimal characters")));
    }
    Ok(value)
}

/// `validate_authority_identity(value, ...)` for any JSON value: a non-string fails like blank text.
pub fn validate_authority_identity_value<'a>(
    value: &'a Json,
    label: &str,
    sha256: bool,
) -> DomainResult<&'a str> {
    match value {
        Json::String(text) => validate_authority_identity(text, label, sha256),
        _ => Err(DomainError::value(format!("{label} must be a non-whitespace string"))),
    }
}

/// `owned_payload(value)`: an owned JSON object, rejecting every other top-level shape.
pub fn owned_payload(value: &Json) -> DomainResult<JsonMap> {
    match value {
        Json::Object(map) => Ok(map.clone()),
        _ => Err(DomainError::type_error("Safe payload must be a JSON object")),
    }
}

str_enum! {
    /// F1 lifecycle states that affect command admission.
    LifecycleState {
        Active = "active",
        Archived = "archived",
        Tombstoned = "tombstoned",
    }
}

str_enum! {
    /// The sole campaign-changing role.
    WriterRole {
        Builder = "builder",
        Dm = "dm",
        None = "none",
    }
}

str_enum! {
    /// Whether one participant has exact package/schema proof or is quarantined.
    ParticipantValidationStatus {
        Validated = "validated",
        Quarantined = "quarantined",
    }
}

/// `lifecycle_writer_is_legal(lifecycle, writer)`.
pub fn lifecycle_writer_is_legal(lifecycle: LifecycleState, writer: WriterRole) -> bool {
    if lifecycle == LifecycleState::Active {
        return matches!(writer, WriterRole::Builder | WriterRole::Dm);
    }
    writer == WriterRole::None
}

str_enum! {
    /// Exact lock membership role.
    PackageRole {
        Primary = "primary",
        Dependency = "dependency",
    }
}

str_enum! {
    /// F1-sized durable TURN lifecycle.
    TurnStatus {
        Open = "open",
        Completed = "completed",
    }
}

str_enum! {
    /// Terminal F1 action outcomes.
    ActionStatus {
        Committed = "committed",
        Failed = "failed",
    }
}

str_enum! {
    /// Chronological records in the sole campaign event sequence.
    CanonicalEventType {
        Think = "think",
        Talk = "talk",
        ActionDeclared = "action_declared",
        ToolCall = "tool_call",
        ToolResult = "tool_result",
        StateMutation = "state_mutation",
        Consequence = "consequence",
        ActionCommitted = "action_committed",
        ActionFailed = "action_failed",
    }
}

str_enum! {
    /// Safe canonical visibility; private provider output has no canonical value.
    EventVisibility {
        InternalSafe = "internal_safe",
        PlayerSafe = "player_safe",
    }
}

str_enum! {
    /// The only prose channels admitted to canonical campaign chronology.
    ProseChannel {
        SafeThink = "safe_think",
        Talk = "talk",
    }
}

str_enum! {
    /// Ruleset-authored guidance for how the GM treats one available mechanic.
    ModelToolBehavior {
        Optional = "optional",
        RequiredEngineTransition = "required_engine_transition",
        /// Requires a Ruleset choice; never execute it with guessed or empty inputs.
        RequiredRulesetDecision = "required_ruleset_decision",
        LeavesPlayerIntentPending = "leaves_player_intent_pending",
        ResolvesPlayerIntent = "resolves_player_intent",
        SupportsPlayerIntent = "supports_player_intent",
    }
}

str_enum! {
    /// Who supplies the next genuine gameplay decision.
    ModelDecisionOwner {
        Player = "player",
        Gm = "gm",
    }
}

str_enum! {
    /// Trace sink identities used by deny-by-default ingress admission.
    InspectorTraceSink {
        Inspector = "inspector",
        Logger = "logger",
        Canonical = "canonical",
        Export = "export",
    }
}

str_enum! {
    /// Ruleset-neutral player projection item kinds.
    PlayerProjectionKind {
        Think = "think",
        Talk = "talk",
        PendingTool = "pending_tool",
        MechanicsChoice = "mechanics_choice",
        ToolCard = "tool_card",
        Retry = "retry",
    }
}

/// One exact dependency link inside a lock entry (`PackageDependency`).
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct PackageDependency {
    package_id: String,
    version: String,
    sha256: String,
    kind: String,
    namespace: String,
}

impl PackageDependency {
    /// Python's default namespace when none is given.
    pub const LEGACY_NAMESPACE: &'static str = "legacy";

    /// `PackageDependency(package_id, version, sha256, kind, namespace)`.
    pub fn new(
        package_id: impl Into<String>,
        version: impl Into<String>,
        sha256: impl Into<String>,
        kind: impl Into<String>,
        namespace: impl Into<String>,
    ) -> DomainResult<Self> {
        let value = Self {
            package_id: package_id.into(),
            version: version.into(),
            sha256: sha256.into(),
            kind: kind.into(),
            namespace: namespace.into(),
        };
        validate_authority_identity(&value.package_id, "Dependency package identity", false)?;
        validate_authority_identity(&value.version, "Dependency version", false)?;
        validate_authority_identity(&value.sha256, "Dependency SHA-256", true)?;
        validate_authority_identity(&value.kind, "Dependency kind", false)?;
        validate_authority_identity(&value.namespace, "Dependency namespace", false)?;
        Ok(value)
    }

    pub fn package_id(&self) -> &str {
        &self.package_id
    }
    pub fn version(&self) -> &str {
        &self.version
    }
    pub fn sha256(&self) -> &str {
        &self.sha256
    }
    pub fn kind(&self) -> &str {
        &self.kind
    }
    pub fn namespace(&self) -> &str {
        &self.namespace
    }

    /// `(package_id, version, sha256)`, the exact identity the dependency names.
    pub fn identity(&self) -> (&str, &str, &str) {
        (&self.package_id, &self.version, &self.sha256)
    }
}

/// One exact member of a campaign package lock (`PackageLockEntry`).
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct PackageLockEntry {
    package_id: String,
    version: String,
    sha256: String,
    role: PackageRole,
    load_order: i64,
    dependencies: Vec<PackageDependency>,
    capabilities: Vec<String>,
}

impl PackageLockEntry {
    /// `PackageLockEntry(...)` with Python's checks, in Python's order.
    pub fn new(
        package_id: impl Into<String>,
        version: impl Into<String>,
        sha256: impl Into<String>,
        role: PackageRole,
        load_order: i64,
        dependencies: Vec<PackageDependency>,
        capabilities: Vec<String>,
    ) -> DomainResult<Self> {
        let value = Self {
            package_id: package_id.into(),
            version: version.into(),
            sha256: sha256.into(),
            role,
            load_order,
            dependencies,
            capabilities,
        };
        validate_authority_identity(&value.package_id, "Package identity", false)?;
        validate_authority_identity(&value.version, "Package version", false)?;
        validate_authority_identity(&value.sha256, "Package SHA-256", true)?;
        for capability in &value.capabilities {
            validate_authority_identity(capability, "Package capability", false)?;
        }
        if value.load_order < 0 {
            return Err(DomainError::value("Package load order cannot be negative"));
        }
        if !all_unique(value.capabilities.iter()) {
            return Err(DomainError::value("Package capabilities must be unique"));
        }
        if !all_unique(value.dependencies.iter().map(|dependency| &dependency.package_id)) {
            return Err(DomainError::value("A package cannot repeat logical dependency membership"));
        }
        Ok(value)
    }

    pub fn package_id(&self) -> &str {
        &self.package_id
    }
    pub fn version(&self) -> &str {
        &self.version
    }
    pub fn sha256(&self) -> &str {
        &self.sha256
    }
    pub fn role(&self) -> PackageRole {
        self.role
    }
    pub fn load_order(&self) -> i64 {
        self.load_order
    }
    pub fn dependencies(&self) -> &[PackageDependency] {
        &self.dependencies
    }
    pub fn capabilities(&self) -> &[String] {
        &self.capabilities
    }

    /// `(package_id, version, sha256)`.
    pub fn identity(&self) -> (&str, &str, &str) {
        (&self.package_id, &self.version, &self.sha256)
    }
}

/// The exact package closure a campaign runs with (`PackageLock`).
#[derive(Debug, Clone, PartialEq, Eq, Hash, Default)]
pub struct PackageLock {
    entries: Vec<PackageLockEntry>,
}

impl PackageLock {
    /// `PackageLock(entries)` with Python's checks, in Python's order.
    pub fn new(entries: Vec<PackageLockEntry>) -> DomainResult<Self> {
        if !entries.is_empty()
            && entries.iter().filter(|entry| entry.role == PackageRole::Primary).count() != 1
        {
            return Err(DomainError::value(
                "A nonempty campaign package lock requires exactly one primary package",
            ));
        }
        if !all_unique(entries.iter().map(|entry| &entry.package_id)) {
            return Err(DomainError::value("A logical package may have only one exact identity in a lock"));
        }
        let orders: Vec<i64> = entries.iter().map(|entry| entry.load_order).collect();
        if !all_unique(orders.iter()) {
            return Err(DomainError::value("Package load order must be unique"));
        }
        if orders.windows(2).any(|pair| pair[0] > pair[1]) {
            return Err(DomainError::value("Package entries must use canonical load order"));
        }
        let identities: HashSet<(&str, &str, &str)> =
            entries.iter().map(PackageLockEntry::identity).collect();
        for entry in &entries {
            for dependency in &entry.dependencies {
                if !identities.contains(&dependency.identity()) {
                    return Err(DomainError::value(
                        "Every exact dependency must be present in the lock closure",
                    ));
                }
            }
        }
        Ok(Self { entries })
    }

    /// The empty lock, `PackageLock(entries=())`.
    pub fn empty() -> Self {
        Self::default()
    }

    pub fn entries(&self) -> &[PackageLockEntry] {
        &self.entries
    }

    /// The single primary entry of a nonempty lock.
    pub fn primary(&self) -> Option<&PackageLockEntry> {
        self.entries.iter().find(|entry| entry.role == PackageRole::Primary)
    }
}

fn all_unique<T: Eq + std::hash::Hash>(items: impl Iterator<Item = T>) -> bool {
    let mut seen = HashSet::new();
    items.into_iter().all(|item| seen.insert(item))
}

/// The fields of a [`Participant`], in Python's order, before validation.
#[derive(Debug, Clone, PartialEq)]
pub struct ParticipantFields {
    pub participant_id: String,
    pub kind: String,
    pub package_id: String,
    pub package_version: String,
    pub data: JsonMap,
    pub package_sha256: Option<String>,
    pub schema_id: Option<String>,
    pub schema_sha256: Option<String>,
    /// Text, because Python converts and checks it in the middle of the other checks.
    /// Python's default is `"quarantined"`.
    pub validation_status: String,
    pub adaptation_id: Option<String>,
}

/// One campaign participant artifact (`Participant`, also `ParticipantArtifact`).
#[derive(Debug, Clone, PartialEq)]
pub struct Participant {
    fields: ParticipantFields,
    status: ParticipantValidationStatus,
}

/// Python's explicit F5 name for the same type.
pub type ParticipantArtifact = Participant;

impl Participant {
    /// `Participant(...)` with Python's checks, in Python's order.
    pub fn new(fields: ParticipantFields) -> DomainResult<Self> {
        validate_authority_identity(&fields.participant_id, "Participant identity", false)?;
        validate_authority_identity(&fields.kind, "Participant kind", false)?;
        validate_authority_identity(&fields.package_id, "Participant package identity", false)?;
        validate_authority_identity(&fields.package_version, "Participant package version", false)?;
        let status = ParticipantValidationStatus::parse(&fields.validation_status)
            .ok_or_else(|| DomainError::value("Participant validation status is invalid"))?;
        if let Some(value) = &fields.package_sha256 {
            validate_authority_identity(value, "Participant package SHA-256", true)?;
        }
        if let Some(value) = &fields.schema_id {
            validate_authority_identity(value, "Participant schema identity", false)?;
        }
        if let Some(value) = &fields.schema_sha256 {
            validate_authority_identity(value, "Participant schema SHA-256", true)?;
        }
        if let Some(value) = &fields.adaptation_id {
            validate_authority_identity(value, "Participant adaptation identity", false)?;
        }
        if status == ParticipantValidationStatus::Validated
            && (fields.package_sha256.is_none()
                || fields.schema_id.is_none()
                || fields.schema_sha256.is_none())
        {
            return Err(DomainError::value(
                "Validated participants require exact package and schema identity",
            ));
        }
        let mut fields = fields;
        fields.validation_status = status.as_str().to_owned();
        Ok(Self { fields, status })
    }

    pub fn participant_id(&self) -> &str {
        &self.fields.participant_id
    }
    pub fn kind(&self) -> &str {
        &self.fields.kind
    }
    pub fn package_id(&self) -> &str {
        &self.fields.package_id
    }
    pub fn package_version(&self) -> &str {
        &self.fields.package_version
    }
    pub fn data(&self) -> &JsonMap {
        &self.fields.data
    }
    pub fn package_sha256(&self) -> Option<&str> {
        self.fields.package_sha256.as_deref()
    }
    pub fn schema_id(&self) -> Option<&str> {
        self.fields.schema_id.as_deref()
    }
    pub fn schema_sha256(&self) -> Option<&str> {
        self.fields.schema_sha256.as_deref()
    }
    pub fn validation_status(&self) -> ParticipantValidationStatus {
        self.status
    }
    pub fn adaptation_id(&self) -> Option<&str> {
        self.fields.adaptation_id.as_deref()
    }
    /// The validated fields, for callers that rebuild a changed participant.
    pub fn fields(&self) -> &ParticipantFields {
        &self.fields
    }
}

/// One namespaced campaign state value (`StateValue`).
#[derive(Debug, Clone, PartialEq)]
pub struct StateValue {
    namespace: String,
    key: String,
    value: Json,
}

impl StateValue {
    /// `StateValue(namespace, key, value)`.
    pub fn new(namespace: impl Into<String>, key: impl Into<String>, value: Json) -> DomainResult<Self> {
        let state = Self { namespace: namespace.into(), key: key.into(), value };
        validate_authority_identity(&state.namespace, "State namespace", false)?;
        validate_authority_identity(&state.key, "State key", false)?;
        Ok(state)
    }
    pub fn namespace(&self) -> &str {
        &self.namespace
    }
    pub fn key(&self) -> &str {
        &self.key
    }
    pub fn value(&self) -> &Json {
        &self.value
    }
}

/// One state write produced by a mechanic (`StateMutation`; no validation in Python).
#[derive(Debug, Clone, PartialEq)]
pub struct StateMutation {
    pub namespace: String,
    pub key: String,
    pub value: Json,
}

/// Replace live character data without changing its exact package or schema.
#[derive(Debug, Clone, PartialEq)]
pub struct ParticipantMutation {
    pub participant_id: String,
    pub data: JsonMap,
}

/// One bounded package-authored option shown by the trusted owner UI.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MechanicsChoiceOption {
    pub option_id: String,
    pub label: String,
}

/// A rules-neutral post-result choice emitted by one deterministic mechanic.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MechanicsChoiceRequest {
    pub choice_id: String,
    pub prompt: String,
    pub options: Vec<MechanicsChoiceOption>,
}

/// One authorized answer retained while the same deterministic action continues.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MechanicsChoiceAnswer {
    pub resolution_id: String,
    pub request: MechanicsChoiceRequest,
    pub option_id: String,
    pub session_id: Option<String>,
}

/// The sole owner-authorized input injected when a pending mechanic resumes.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MechanicsChoiceResolution {
    pub choice_id: String,
    pub option_id: String,
    pub expected_request: MechanicsChoiceRequest,
    pub prior_answers: Vec<MechanicsChoiceAnswer>,
}

/// Immutable data-only input; it deliberately exposes no database authority.
#[derive(Debug, Clone, PartialEq)]
pub struct MechanicContext {
    pub campaign_id: String,
    pub turn_id: String,
    pub action_id: String,
    pub state: JsonMap,
    pub parameters: JsonMap,
    pub participants: JsonMap,
    pub choice_resolution: Option<MechanicsChoiceResolution>,
}

/// What one mechanic returns (`MechanicOutcome`).
#[derive(Debug, Clone, PartialEq, Default)]
pub struct MechanicOutcome {
    pub result: JsonMap,
    pub mutations: Vec<StateMutation>,
    pub consequences: Vec<JsonMap>,
    pub choice: Option<MechanicsChoiceRequest>,
    pub participant_mutations: Vec<ParticipantMutation>,
}

/// `_is_reserved_canonical_channel(name)`: private channel families after separator and
/// camel-case normalisation.
///
/// Python inserts spaces at camel-case boundaries, case-folds, splits on `[^a-z0-9]+` and
/// joins the tokens again. The spaces and separators all vanish in the join, so the compact
/// form is the ASCII lowercase letters and digits of the case-folded name, in order.
pub fn is_reserved_canonical_channel(name: &str) -> bool {
    let compact: String =
        pystr::casefold(name).chars().filter(|c| c.is_ascii_lowercase() || c.is_ascii_digit()).collect();
    let has = |needle: &str| compact.contains(needle);
    let private_qualifier =
        ["private", "raw", "provider", "chain", "hidden", "internal"].iter().any(|q| has(q));
    if has("reasoning") && private_qualifier {
        return true;
    }
    if has("thought") && private_qualifier {
        return true;
    }
    if has("monologue") && private_qualifier {
        return true;
    }
    if has("scratchpad") {
        return true;
    }
    if (has("modelresponse") || has("modeloutput")) && private_qualifier {
        return true;
    }
    if has("chainofthought") || has("thoughtchain") {
        return true;
    }
    if has("diagnostic") {
        return true;
    }
    if has("traceback") {
        return true;
    }
    if has("stacktrace") || has("tracestack") {
        return true;
    }
    has("trace") && (has("inspector") || has("private"))
}

/// `canonical_payload_is_safe(value)`: no private or internal structural channel at any depth.
pub fn canonical_payload_is_safe(value: &Json) -> bool {
    match value {
        Json::Object(map) => map_is_safe(map),
        Json::Array(items) => items.iter().all(canonical_payload_is_safe),
        _ => true,
    }
}

fn map_is_safe(map: &JsonMap) -> bool {
    map.iter().all(|(key, item)| !is_reserved_canonical_channel(key) && canonical_payload_is_safe(item))
}

/// `mechanic_outcome_is_safe(outcome)`: validate every successful mechanic output before any
/// canonical write. Python's `isinstance` checks are guaranteed by the Rust types.
pub fn mechanic_outcome_is_safe(outcome: &MechanicOutcome) -> bool {
    if outcome.result.contains_key("mechanics_choice_answers") {
        return false;
    }
    if !map_is_safe(&outcome.result) {
        return false;
    }
    if let Some(choice) = &outcome.choice {
        if !(2..=16).contains(&choice.options.len()) || choice.prompt.len() > 4096 {
            return false;
        }
        if validate_authority_identity(&choice.choice_id, "Mechanics choice identity", false).is_err() {
            return false;
        }
        let mut option_ids = HashSet::new();
        for option in &choice.options {
            if option.label.is_empty() || option.label.len() > 1024 {
                return false;
            }
            if validate_authority_identity(&option.option_id, "Mechanics option identity", false).is_err() {
                return false;
            }
            if !option_ids.insert(option.option_id.as_str()) {
                return false;
            }
        }
        if !outcome.mutations.is_empty()
            || !outcome.consequences.is_empty()
            || !outcome.participant_mutations.is_empty()
        {
            return false;
        }
    }
    let mut participant_ids = HashSet::new();
    for mutation in &outcome.participant_mutations {
        if mutation.participant_id.is_empty()
            || !participant_ids.insert(mutation.participant_id.as_str())
            || !map_is_safe(&mutation.data)
        {
            return false;
        }
    }
    if let Some(status) = outcome.result.get("status") {
        if !status.is_null() && !status.is_string() {
            return false;
        }
    }
    for consequence in &outcome.consequences {
        if !map_is_safe(consequence) {
            return false;
        }
        match consequence.get("kind") {
            None | Some(Json::Null) => {}
            Some(Json::String(kind)) if kind == "participant_updated" || kind.is_empty() => return false,
            Some(Json::String(_)) => {}
            Some(_) => return false,
        }
    }
    for mutation in &outcome.mutations {
        if is_reserved_canonical_channel(&mutation.namespace)
            || is_reserved_canonical_channel(&mutation.key)
            || !canonical_payload_is_safe(&mutation.value)
        {
            return false;
        }
    }
    true
}

/// `validate_response_blocks(value)`: one ordered player-safe GM response block list.
pub fn validate_response_blocks(value: &Json) -> DomainResult<Vec<JsonMap>> {
    let items = match value {
        Json::Array(items) if !items.is_empty() => items,
        _ => return Err(DomainError::value("Response blocks must be a non-empty sequence")),
    };
    let mut blocks = Vec::with_capacity(items.len());
    for item in items {
        let Json::Object(item) = item else {
            return Err(DomainError::value("Each response block must be an object"));
        };
        let kind = item.get("kind").and_then(Json::as_str);
        let expected: &[&str] =
            if kind == Some("npc_dialogue") { &["kind", "speaker_label", "text"] } else { &["kind", "text"] };
        let keys_match = item.len() == expected.len() && expected.iter().all(|key| item.contains_key(*key));
        let text_ok =
            item.get("text").and_then(Json::as_str).is_some_and(|text| !pystr::strip(text).is_empty());
        if !matches!(kind, Some("narration" | "npc_dialogue" | "ooc")) || !keys_match || !text_ok {
            return Err(DomainError::value("Response block fields are invalid"));
        }
        if kind == Some("npc_dialogue")
            && !item
                .get("speaker_label")
                .and_then(Json::as_str)
                .is_some_and(|label| !pystr::strip(label).is_empty())
        {
            return Err(DomainError::value("NPC dialogue requires a safe speaker label"));
        }
        blocks.push(item.clone());
    }
    Ok(blocks)
}

/// `response_blocks_text(blocks)`: the stable labelled-text fallback.
pub fn response_blocks_text(blocks: &Json) -> DomainResult<String> {
    let validated = validate_response_blocks(blocks)?;
    let text = |block: &JsonMap, key: &str| block[key].as_str().unwrap_or_default().to_owned();
    Ok(validated
        .iter()
        .map(|block| match block["kind"].as_str() {
            Some("npc_dialogue") => format!("{}: {}", text(block, "speaker_label"), text(block, "text")),
            Some("ooc") => format!("OOC — Sylvia: {}", text(block, "text")),
            _ => text(block, "text"),
        })
        .collect::<Vec<_>>()
        .join("\n\n"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn identity_messages_match_python() {
        let message =
            |value: &str, sha: bool| validate_authority_identity(value, "X", sha).unwrap_err().to_string();
        assert_eq!(message(" ", false), "X must be a non-whitespace string");
        assert_eq!(message("\x1ca", false), "X must not have leading or trailing whitespace");
        assert_eq!(message("ABC", true), "X must be 64 lowercase hexadecimal characters");
        assert!(validate_authority_identity("a b", "X", false).is_ok());
    }

    #[test]
    fn writer_matrix() {
        assert!(lifecycle_writer_is_legal(LifecycleState::Active, WriterRole::Dm));
        assert!(!lifecycle_writer_is_legal(LifecycleState::Active, WriterRole::None));
        assert!(lifecycle_writer_is_legal(LifecycleState::Archived, WriterRole::None));
        assert!(!lifecycle_writer_is_legal(LifecycleState::Tombstoned, WriterRole::Builder));
    }

    #[test]
    fn enums_round_trip() {
        for role in PackageRole::ALL {
            assert_eq!(PackageRole::parse(role.as_str()), Some(*role));
        }
        assert_eq!(PackageRole::parse("Primary"), None);
    }
}
