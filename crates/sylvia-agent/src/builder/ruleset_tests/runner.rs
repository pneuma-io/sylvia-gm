//! `agent/_ruleset_test_runner.py`: execute one saved scenario in a disposable worker.
//!
//! Ported: `_failure_message`, `ScenarioDice`, `_assertions`, and `run_scenario` up to the
//! campaign it plays in: the fresh database, the exact archive installed, its primary Ruleset's
//! participant declarations and each scripted participant built against them.
//!
//! Not yet ported, so `run_scenario` answers `rust_port_pending` there as a failed scenario:
//! creating the campaign and its session (the owner's `create_campaign` and
//! `create_agent_session`), the steps (`gm_turn` and `player_action_response` through lane 4's
//! `CampaignSessionLoop.run` (T5) with the context ledger's callable-tools records (L1-L3), and
//! `player_action` through the owner's command), the observations (`_plain` and
//! `_with_engine_results`) and the dice in force while a step runs. Python's nested
//! `ExceptionGroup` and `MCPError` failures come from the turn, so they come with it.

use super::coverage::is_int;
use crate::builder::is_authority_error;
use std::path::Path;
use std::sync::{Arc, Mutex, PoisonError};
use sylvia_base::{CodedError, Json, JsonMap};
use sylvia_domain::authority::{owned_payload, Participant, ParticipantFields};
use sylvia_domain::pyops::py_eq;
use sylvia_domain::DomainError;
use sylvia_engine::dice::RollSource;
use sylvia_engine::pyerror::{PyError, PyResult};
use sylvia_store::{AuthorityStore, StoreError};

/// Where the port stops, as the scenario's failure.
const PORT_PENDING: &str = "rust_port_pending";

/// What a scenario raised: `ScenarioScriptError(message)`, or any other exception by its
/// class, with the code of an `AuthorityError`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ScenarioError {
    /// `ScenarioScriptError`: the run differs from the saved script; its message is shown.
    Script(String),
    /// Another exception: `type(error).__name__` and, for an `AuthorityError`, its `code`.
    Raised { class: String, code: Option<String> },
}

impl ScenarioError {
    /// An exception of `class` without a code.
    pub fn raised(class: &str) -> Self {
        Self::Raised { class: class.to_owned(), code: None }
    }
}

impl From<CodedError> for ScenarioError {
    /// A coded refusal: its Python class (`CommandRejected` when the port names none).
    fn from(error: CodedError) -> Self {
        let class = error.error_type.unwrap_or("CommandRejected");
        let code = is_authority_error(class).then_some(error.code);
        Self::Raised { class: class.to_owned(), code }
    }
}

impl From<DomainError> for ScenarioError {
    fn from(error: DomainError) -> Self {
        let class = error.python_class();
        let code = is_authority_error(class).then(|| error.code().unwrap_or("command_rejected").to_owned());
        Self::Raised { class: class.to_owned(), code }
    }
}

impl From<StoreError> for ScenarioError {
    /// A store failure. A `sqlite3.Error` is named by its base class and any other uncoded
    /// exception as `Exception`, where Python names the exact class (`differences.md`, the row
    /// for `run_scenario`'s store faults).
    fn from(error: StoreError) -> Self {
        match error {
            StoreError::Coded(error) => error.into(),
            StoreError::Backup(failure) => failure.to_coded().into(),
            StoreError::Sqlite(_) => Self::raised("DatabaseError"),
            StoreError::Other(_) => Self::raised("Exception"),
            StoreError::NotPorted(what) => port_pending(what).into(),
        }
    }
}

/// The refusal where the port stops.
fn port_pending(what: &str) -> CodedError {
    CodedError::new(PORT_PENDING, format!("not yet ported: {what}"))
}

/// `re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code)`.
fn is_safe_code(code: &str) -> bool {
    let bytes = code.as_bytes();
    matches!(bytes.first(), Some(b'a'..=b'z'))
        && bytes.len() <= 80
        && bytes.iter().all(|byte| matches!(byte, b'a'..=b'z' | b'0'..=b'9' | b'_'))
}

/// `_failure_message(error)`: expose the error's type and authority code, never raw runtime
/// messages. A `ScenarioScriptError` is the script's own message.
pub fn failure_message(error: &ScenarioError) -> String {
    match error {
        ScenarioError::Script(message) => message.clone(),
        ScenarioError::Raised { class, code } => match code.as_deref().filter(|code| is_safe_code(code)) {
            Some(code) => format!("{class} ({code})"),
            None => class.clone(),
        },
    }
}

/// One scripted die face.
#[derive(Debug, Clone)]
struct Roll {
    sides: Json,
    value: Json,
}

#[derive(Debug, Default)]
struct DiceState {
    rolls: Vec<Roll>,
    position: usize,
    failure: Option<String>,
}

/// `ScenarioDice(rolls)`: supply individual die faces to the normal dice helpers inside the
/// isolated worker only. Clones share one script, so the run can read it back after the dice
/// streams took it (`Dice::use_roll_source`).
#[derive(Debug, Clone, Default)]
pub struct ScenarioDice {
    state: Arc<Mutex<DiceState>>,
}

impl ScenarioDice {
    /// `ScenarioDice(scenario["rolls"])`: each `{"sides", "value"}` the suite admitted.
    pub fn new(rolls: &[Json]) -> Self {
        let field = |roll: &Json, key: &str| roll.get(key).cloned().unwrap_or(Json::Null);
        let rolls = rolls
            .iter()
            .map(|roll| Roll { sides: field(roll, "sides"), value: field(roll, "value") })
            .collect();
        Self { state: Arc::new(Mutex::new(DiceState { rolls, position: 0, failure: None })) }
    }

    fn state(&self) -> std::sync::MutexGuard<'_, DiceState> {
        self.state.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// `rolls.failure`: the first mismatch, kept even if the game caught the exception.
    pub fn failure(&self) -> Option<String> {
        self.state().failure.clone()
    }

    /// `assert_finished()`: every scripted face was used, and none mismatched.
    pub fn assert_finished(&self) -> Result<(), ScenarioError> {
        let state = self.state();
        if let Some(failure) = &state.failure {
            return Err(ScenarioError::Script(failure.clone()));
        }
        if state.position != state.rolls.len() {
            return Err(ScenarioError::Script("The scenario finished with unused scripted rolls.".into()));
        }
        Ok(())
    }
}

/// A JSON integer as Python's `int`, for the arithmetic `randint` does on it.
fn integer(value: &Json) -> Option<i128> {
    is_int(value).then(|| value.as_i64().map(i128::from).or_else(|| value.as_u64().map(i128::from))).flatten()
}

impl RollSource for ScenarioDice {
    /// `randint(a, b)`: translate saved one-based faces for both ordinary dice and zero-based
    /// table draws.
    fn randint(&mut self, a: i128, b: i128) -> PyResult<i128> {
        let mut state = self.state();
        let fail = |state: &mut DiceState, message: &str| {
            state.failure = Some(message.to_owned());
            Err(PyError::value(message))
        };
        let Some(roll) = state.rolls.get(state.position).cloned() else {
            return fail(&mut state, "The game requested more rolls than the scenario supplies.");
        };
        state.position += 1;
        // `roll["sides"] != b - a + 1`: a JSON integer compares by value.
        if integer(&roll.sides) != Some(b - a + 1) {
            return fail(&mut state, "The requested die size differs from the scripted roll.");
        }
        let value = integer(&roll.value).expect("the suite admits integer roll values");
        Ok(a + value - 1)
    }
}

/// Python's `type(value)` for a JSON value.
fn python_type(value: &Json) -> u8 {
    match value {
        Json::Null => 0,
        Json::Bool(_) => 1,
        Json::Number(_) if is_int(value) => 2,
        Json::Number(_) => 3,
        Json::String(_) => 4,
        Json::Array(_) => 5,
        Json::Object(_) => 6,
    }
}

/// `actual[part]` for the parts `_assertions` follows: a key of an object, or an in-range
/// integer index of a list.
fn child<'a>(actual: &'a Json, part: &Json) -> Option<&'a Json> {
    match (actual, part) {
        (Json::Object(map), Json::String(key)) => map.get(key),
        (Json::Array(items), _) if is_int(part) => {
            part.as_i64().and_then(|index| usize::try_from(index).ok()).and_then(|index| items.get(index))
        }
        _ => None,
    }
}

/// `_assertions(expected, observations)`: compare exact saved expectations; missing paths never
/// compare equal to null. The top-level type must match exactly; within it Python's `==`
/// applies (`1 == 1.0` and `True == 1` inside a list or object).
///
/// An assertion naming no observation raises `KeyError`, as `observations[subject]` does.
pub fn assertions(expected: &[Json], observations: &JsonMap) -> Result<Vec<Json>, ScenarioError> {
    let mut failures = Vec::new();
    for assertion in expected {
        let field = |key: &str| assertion.get(key).cloned().unwrap_or(Json::Null);
        let subject = field("subject");
        let Some(mut actual) = subject.as_str().and_then(|subject| observations.get(subject)) else {
            return Err(ScenarioError::raised("KeyError"));
        };
        let path = field("path");
        let mut missing = false;
        for part in path.as_array().into_iter().flatten() {
            match child(actual, part) {
                Some(next) => actual = next,
                None => {
                    missing = true;
                    break;
                }
            }
        }
        let equals = field("equals");
        if missing || python_type(actual) != python_type(&equals) || !py_eq(actual, &equals) {
            let mut failure = JsonMap::new();
            failure.insert("subject".into(), subject);
            failure.insert("path".into(), path);
            failure.insert("expected".into(), equals);
            failure.insert("actual".into(), if missing { Json::Null } else { actual.clone() });
            failure.insert("missing".into(), Json::Bool(missing));
            failures.push(Json::Object(failure));
        }
    }
    Ok(failures)
}

/// `run_scenario(archive, scenario, directory)`: the worker-only entry. Its fresh database in
/// `directory` cannot touch live campaigns. Never fails: what the scenario raised is its
/// failed result, `{"id", "status": "failed", "steps", "failures": [{"message"}]}`.
///
/// Until the campaign and its steps are ported (see the module), every scenario that gets past
/// its participants fails with `CommandRejected (rust_port_pending)`.
pub async fn run_scenario(archive: &[u8], scenario: &JsonMap, directory: &Path) -> JsonMap {
    let (archive, owned, directory) = (archive.to_vec(), scenario.clone(), directory.to_path_buf());
    let error = match tokio::task::spawn_blocking(move || prepare_campaign(&archive, &owned, &directory))
        .await
    {
        Ok(Ok(())) => port_pending("creating the scenario's campaign (the owner's create_campaign)").into(),
        Ok(Err(error)) => error,
        // The store panicked: Python's exception escapes as a failed scenario.
        Err(_) => ScenarioError::raised("Exception"),
    };
    let mut failure = JsonMap::new();
    failure.insert("message".into(), Json::String(failure_message(&error)));
    let mut result = JsonMap::new();
    result.insert("id".into(), scenario.get("id").cloned().unwrap_or(Json::Null));
    result.insert("status".into(), "failed".into());
    result.insert("steps".into(), Json::Array(Vec::new()));
    result.insert("failures".into(), Json::Array(vec![Json::Object(failure)]));
    result
}

/// The part of `run_scenario` before `create_campaign`: open the fresh owner, install the exact
/// archive, and build each scripted participant against its primary Ruleset's declarations.
fn prepare_campaign(archive: &[u8], scenario: &JsonMap, directory: &Path) -> Result<(), ScenarioError> {
    let store = AuthorityStore::open_for_application_owner(&directory.join("campaign.sqlite3"), None)?;
    let candidate = sylvia_app::package_library::install_package_archive(&store, archive, "scenario")?;
    // `next(entry for entry in ... if entry.role.value == "primary")`.
    let primary = candidate.package_lock().primary().ok_or_else(|| ScenarioError::raised("StopIteration"))?;
    let (package_id, version, sha256) = primary.identity();
    let declarations = store.exact_participant_declarations(package_id, version, sha256)?;
    let text =
        |record: &Json, key: &str| record.get(key).and_then(Json::as_str).unwrap_or_default().to_owned();
    for record in scenario.get("participants").and_then(Json::as_array).into_iter().flatten() {
        let schema_id = text(record, "schema_id");
        let matches: Vec<_> = declarations.iter().filter(|item| item.schema_id == schema_id).collect();
        if matches.len() != 1 {
            return Err(ScenarioError::Script(
                "Participant schema is not declared by the tested Ruleset.".into(),
            ));
        }
        let data = owned_payload(record.get("data").unwrap_or(&Json::Null))?;
        Participant::new(ParticipantFields {
            participant_id: text(record, "id"),
            kind: text(record, "kind"),
            package_id: package_id.to_owned(),
            package_version: version.to_owned(),
            data,
            package_sha256: Some(sha256.to_owned()),
            schema_id: Some(schema_id),
            schema_sha256: Some(matches[0].schema_sha256.clone()),
            validation_status: "validated".into(),
            adaptation_id: None,
        })?;
    }
    Ok(())
}
