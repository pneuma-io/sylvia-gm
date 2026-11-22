//! `application/actor_decisions.py`: a Ruleset's isolated participant decision, projected
//! without assembling a GM prompt.

use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{CampaignState, ModelDecisionBoundary, ModelDecisionOwner, PackageRole};
use sylvia_domain::error::{DomainError, DomainResult};
use sylvia_domain::schema_admission::validate_parameters;

/// `ActorDecisionAction`.
#[derive(Debug, Clone, PartialEq)]
pub struct ActorDecisionAction {
    pub identity: String,
    pub label: String,
    pub operation: String,
    pub arguments: JsonMap,
}

/// `ActorDecisionRequest`.
#[derive(Debug, Clone, PartialEq)]
pub struct ActorDecisionRequest {
    pub campaign_id: String,
    pub turn_id: String,
    pub package_id: String,
    pub package_version: String,
    pub package_sha256: String,
    pub actor_id: String,
    pub actor_name: String,
    pub revision: i64,
    pub decision_id: String,
    pub instructions: String,
    pub persona: String,
    pub observation: JsonMap,
    pub actions: Vec<ActorDecisionAction>,
}

impl ActorDecisionRequest {
    /// `observation_payload()`: the envelope's permitted observation and offered choices.
    pub fn observation_payload(&self) -> JsonMap {
        let mut package = JsonMap::new();
        package.insert("id".into(), self.package_id.clone().into());
        package.insert("version".into(), self.package_version.clone().into());
        package.insert("sha256".into(), self.package_sha256.clone().into());
        let mut actor = JsonMap::new();
        actor.insert("id".into(), self.actor_id.clone().into());
        actor.insert("name".into(), self.actor_name.clone().into());
        actor.insert("persona".into(), self.persona.clone().into());
        let actions = self
            .actions
            .iter()
            .map(|action| {
                let mut entry = JsonMap::new();
                entry.insert("id".into(), action.identity.clone().into());
                entry.insert("label".into(), action.label.clone().into());
                entry.insert("operation".into(), action.operation.clone().into());
                entry.insert("arguments".into(), Json::Object(action.arguments.clone()));
                Json::Object(entry)
            })
            .collect();
        let mut payload = JsonMap::new();
        payload.insert("campaign_id".into(), self.campaign_id.clone().into());
        payload.insert("turn_id".into(), self.turn_id.clone().into());
        payload.insert("package".into(), Json::Object(package));
        payload.insert("actor".into(), Json::Object(actor));
        payload.insert("revision".into(), self.revision.into());
        payload.insert("decision_id".into(), self.decision_id.clone().into());
        payload.insert("observation".into(), Json::Object(self.observation.clone()));
        payload.insert("actions".into(), Json::Array(actions));
        payload
    }
}

/// `_path(value, path)`.
fn path<'v>(value: &'v Json, steps: &Json) -> DomainResult<&'v Json> {
    let mut current = value;
    for step in steps.as_array().map(Vec::as_slice).unwrap_or_default() {
        let key = step.as_str().unwrap_or_default();
        current = match current.as_object().and_then(|map| map.get(key)) {
            Some(next) => next,
            None => return Err(DomainError::command_rejected("Ruleset actor observation is unavailable")),
        };
    }
    Ok(current)
}

/// `public_actor_observation(snapshot, declaration)`: only the Ruleset-declared public view.
pub fn public_actor_observation(snapshot: &CampaignState, declaration: &JsonMap) -> DomainResult<JsonMap> {
    let reference = declaration.get("public_observation").unwrap_or(&Json::Null);
    let namespace = reference["namespace"].as_str().unwrap_or_default();
    let key = reference["key"].as_str().unwrap_or_default();
    let value = match snapshot.state.get(&format!("{namespace}.{key}")) {
        Some(value) => value.clone(),
        None => {
            let (root, rest) = key.split_once('.').unwrap_or((key, ""));
            let start = snapshot.state.get(&format!("{namespace}.{root}")).cloned().unwrap_or(Json::Null);
            let steps = if rest.is_empty() { Json::Array(Vec::new()) } else { rest.split('.').map(Json::from).collect() };
            path(&start, &steps)?.clone()
        }
    };
    match value {
        Json::Object(map) => Ok(map),
        _ => Err(DomainError::command_rejected("Ruleset public actor observation must be an object")),
    }
}

/// `project_actor_decision(snapshot, boundary, declaration, turn_id=..., tool_schemas=...)`: one
/// participant's offered operations and observation, bound to the same authority revision.
pub fn project_actor_decision(
    snapshot: &CampaignState,
    boundary: Option<&ModelDecisionBoundary>,
    declaration: &JsonMap,
    turn_id: &str,
    tool_schemas: &JsonMap,
) -> DomainResult<Option<ActorDecisionRequest>> {
    let Some(boundary) = boundary.filter(|boundary| boundary.owner == ModelDecisionOwner::Gm) else {
        return Ok(None);
    };
    let actor_id = match boundary.actor_id.as_deref() {
        Some(actor_id) if !actor_id.is_empty() => actor_id.to_owned(),
        _ => return Err(DomainError::command_rejected("Isolated participant requires one active actor")),
    };
    let primary = snapshot
        .package_lock
        .entries()
        .iter()
        .find(|entry| entry.role() == PackageRole::Primary)
        .ok_or_else(|| DomainError::Uncaught { error_type: "StopIteration", message: String::new() })?;
    let actor_state = Json::Object(boundary.actor_state.clone());
    let observation = path(&actor_state, declaration.get("observation_path").unwrap_or(&Json::Null))?;
    let persona = match declaration.get("persona_path") {
        Some(steps) => path(&actor_state, steps)?.clone(),
        None => Json::String(String::new()),
    };
    let rows = path(&actor_state, declaration.get("actions_path").unwrap_or(&Json::Null))?;
    let invalid = || DomainError::command_rejected("Ruleset isolated participant observation or actions are invalid");
    let Json::Object(observation) = observation else { return Err(invalid()) };
    let Json::String(persona) = persona else { return Err(invalid()) };
    if persona.len() > 8192 {
        return Err(invalid());
    }
    let Json::Array(rows) = rows else { return Err(invalid()) };
    if !(1..=256).contains(&rows.len()) {
        return Err(invalid());
    }
    let mut actions = Vec::new();
    let mut identities: Vec<String> = Vec::new();
    for row in rows {
        let Json::Object(row) = row else {
            return Err(DomainError::command_rejected("Ruleset isolated participant offer is invalid"));
        };
        let keys: std::collections::BTreeSet<&str> = row.keys().map(String::as_str).collect();
        if keys != ["arguments", "id", "label", "operation"].into_iter().collect() {
            return Err(DomainError::command_rejected("Ruleset isolated participant offer is invalid"));
        }
        let text = |key: &str| row[key].as_str().filter(|value| !value.is_empty() && value.len() <= 256);
        let unavailable = || DomainError::command_rejected("Ruleset isolated participant operation is unavailable");
        let (Some(identity), Some(label), Some(operation)) = (text("id"), text("label"), text("operation")) else {
            return Err(unavailable());
        };
        if identities.iter().any(|known| known == identity) || !tool_schemas.contains_key(operation) {
            return Err(unavailable());
        }
        let Json::Object(arguments) = &row["arguments"] else { return Err(unavailable()) };
        let schema = tool_schemas[operation].as_object().cloned().unwrap_or_default();
        validate_parameters(arguments, &schema)?;
        actions.push(ActorDecisionAction {
            identity: identity.to_owned(),
            label: label.to_owned(),
            operation: operation.to_owned(),
            arguments: arguments.clone(),
        });
        identities.push(identity.to_owned());
    }
    let mut request = ActorDecisionRequest {
        campaign_id: snapshot.campaign_id.clone(),
        turn_id: turn_id.to_owned(),
        package_id: primary.package_id().to_owned(),
        package_version: primary.version().to_owned(),
        package_sha256: primary.sha256().to_owned(),
        actor_name: boundary.actor_name.clone().filter(|name| !name.is_empty()).unwrap_or_else(|| actor_id.clone()),
        actor_id,
        revision: snapshot.revision,
        decision_id: String::new(),
        instructions: declaration.get("instructions").and_then(Json::as_str).unwrap_or_default().to_owned(),
        persona,
        observation: observation.clone(),
        actions,
    };
    let mut envelope = request.observation_payload();
    envelope.insert("instructions".into(), request.instructions.clone().into());
    let body = pyjson::dumps(&Json::Object(envelope), Dump::DEFAULT.sorted(true).ascii(false).separators(",", ":"));
    if body.len() > 128 * 1024 {
        return Err(DomainError::command_rejected("Ruleset isolated participant envelope exceeds its bound"));
    }
    let identity = Json::Array(vec![
        request.campaign_id.clone().into(),
        request.turn_id.clone().into(),
        request.package_id.clone().into(),
        request.package_version.clone().into(),
        request.package_sha256.clone().into(),
        request.actor_id.clone().into(),
        request.revision.into(),
    ]);
    let identity = pyjson::dumps(&identity, Dump::DEFAULT.separators(",", ":"));
    request.decision_id = sylvia_base::hash::sha256_hex(identity.as_bytes());
    Ok(Some(request))
}
