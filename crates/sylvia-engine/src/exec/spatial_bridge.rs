//! `application/package_runtime.py`'s `AuthorityMemoryStateBridge`, its spatial part: the
//! package-local spatial engines one mechanic execution opens, their snapshots, query results,
//! mutations and evidence, and which context variables are spatial and so private.
//!
//! The bridge reads each scene from package state itself (and, for a player's preview, through
//! the player-known scene boundary); this part takes the scene value it read.

use super::error::ExecError;
use crate::expr::value::Value;
use crate::spatial::{
    admit_spatial_query, admit_spatial_scene, mutate_spatial_scene, AdmittedSpatialScene, SpatialEngine,
    SpatialError,
};
use indexmap::IndexMap;
use std::collections::{HashMap, HashSet};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::package_library::canonical_json_bytes;

/// `_MAX_SPATIAL_EVIDENCE`.
const MAX_SPATIAL_EVIDENCE: usize = 128;
/// `_MAX_SPATIAL_RESULT_BYTES`.
const MAX_SPATIAL_RESULT_BYTES: usize = 2 * 1024 * 1024;

/// A spatial exception as Python raises it from the bridge: `SpatialValidationError`, or one
/// Python lets through.
pub fn spatial_error(error: SpatialError) -> ExecError {
    match error {
        SpatialError::Validation(error) => {
            ExecError::Python { class: "SpatialValidationError", message: error.to_string() }
        }
        SpatialError::Uncaught { error_type, message } => ExecError::Python { class: error_type, message },
    }
}

/// `scene_path.partition(".")` with `root == "state"`: the scene's field in package state.
pub fn scene_field(scene_path: &str) -> Result<&str, ExecError> {
    match scene_path.split_once('.') {
        Some(("state", field)) if !field.is_empty() => Ok(field),
        _ => Err(ExecError::package_runtime("Spatial scene must use a package-owned state path")),
    }
}

/// The scene value read from package state: a mapping, or Python's "unavailable" refusal.
pub fn scene_value(value: &Value) -> Result<Json, ExecError> {
    match value {
        Value::Dict(_) => super::bridge::json_safe(value),
        _ => Err(ExecError::package_runtime("Package-owned spatial scene is unavailable")),
    }
}

/// `_spatial_proof_number(value)`: geometry's float arithmetic in proof keys, `0.0` for any zero.
fn proof_number(value: &Json) -> Json {
    let number = value.as_f64().expect("an admitted number");
    let number = if number == 0.0 { 0.0 } else { number };
    Json::from(serde_json::Number::from_f64(number).expect("a finite number"))
}

/// The spatial state of one bridge.
#[derive(Default)]
pub struct SpatialBridge {
    engines: IndexMap<String, SpatialEngine>,
    snapshots: HashMap<String, AdmittedSpatialScene>,
    recorded: HashSet<(String, String)>,
    private: HashSet<String>,
    evidence: Vec<JsonMap>,
    query_results: HashMap<(String, String, bool, Vec<u8>), JsonMap>,
    state_parameters: IndexMap<String, String>,
    aggregates: IndexMap<String, Vec<String>>,
}

impl std::fmt::Debug for SpatialBridge {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("SpatialBridge")
            .field("scenes", &self.engines.keys().collect::<Vec<_>>())
            .field("evidence", &self.evidence.len())
            .finish()
    }
}

impl SpatialBridge {
    /// `_spatial_engine(scene_path)` past reading the scene: one engine per scene path, advanced
    /// when the scene's snapshot changed.
    pub fn engine(&mut self, scene_path: &str, scene: &Json) -> Result<&mut SpatialEngine, ExecError> {
        let admitted = admit_spatial_scene(scene).map_err(spatial_error)?;
        match self.engines.get_index_of(scene_path) {
            None => {
                let engine = SpatialEngine::new(scene).map_err(spatial_error)?;
                self.engines.insert(scene_path.to_owned(), engine);
            }
            Some(index) => {
                let engine = &mut self.engines[index];
                if admitted.snapshot_id != engine.snapshot().snapshot_id {
                    engine.replace_scene(scene).map_err(spatial_error)?;
                }
            }
        }
        Ok(&mut self.engines[scene_path])
    }

    /// `_record_spatial_evidence(operation=..., query=..., result=...)`.
    fn record_evidence(&mut self, operation: &str, query: &Json, result: &JsonMap) -> Result<(), ExecError> {
        if self.evidence.len() >= MAX_SPATIAL_EVIDENCE {
            return Err(ExecError::package_runtime("Spatial query evidence exceeds the action limit"));
        }
        let bytes = canonical_json_bytes(&Json::Object(result.clone()));
        if bytes.len() > MAX_SPATIAL_RESULT_BYTES {
            return Err(ExecError::package_runtime("Spatial query result exceeds the action byte limit"));
        }
        let digest: String = sylvia_base::hash::sha256(&bytes).iter().map(|b| format!("{b:02x}")).collect();
        let mut evidence = JsonMap::new();
        evidence.insert("operation".into(), Json::from(operation));
        for key in ["map_id", "geometry_revision", "snapshot_id", "algorithm_version"] {
            evidence.insert(key.into(), result[key].clone());
        }
        evidence.insert("query".into(), query.clone());
        evidence.insert("ordered_result_sha256".into(), Json::String(digest));
        for key in ["target", "before", "after"] {
            if let Some(value) = result.get(key) {
                evidence.insert(key.into(), value.clone());
            }
        }
        self.evidence.push(evidence);
        Ok(())
    }

    /// `spatial_snapshot(scene_path)`: the current snapshot's provenance, recorded as evidence
    /// once per scene and snapshot.
    pub fn snapshot(&mut self, scene_path: &str, scene: &Json) -> Result<Json, ExecError> {
        let snapshot = self.engine(scene_path, scene)?.snapshot().clone();
        let mut result = JsonMap::new();
        result.insert("kind".into(), Json::from("snapshot"));
        result.insert("map_id".into(), Json::from(snapshot.map_id.as_str()));
        result.insert("geometry_revision".into(), Json::from(snapshot.geometry_revision));
        result.insert("snapshot_id".into(), Json::from(snapshot.snapshot_id.as_str()));
        result.insert("algorithm_version".into(), Json::from(snapshot.algorithm_version));
        let key = (scene_path.to_owned(), snapshot.snapshot_id.clone());
        self.snapshots.insert(snapshot.snapshot_id.clone(), snapshot.clone());
        if !self.recorded.contains(&key) {
            let mut query = JsonMap::new();
            query.insert("kind".into(), Json::from("snapshot"));
            query.insert("map_id".into(), Json::from(snapshot.map_id.as_str()));
            self.record_evidence("snapshot", &Json::Object(query), &result)?;
            self.recorded.insert(key);
        }
        Ok(Json::Object(result))
    }

    /// `spatial_query(scene_path, operation=..., query=..., snapshot=...)` past its input check:
    /// one admitted query, answered from this action's results when the same proof was already
    /// resolved, otherwise resolved and recorded as evidence.
    pub fn query(
        &mut self,
        scene_path: &str,
        scene: &Json,
        operation: &str,
        query: &JsonMap,
        snapshot: &Json,
    ) -> Result<Json, ExecError> {
        self.engine(scene_path, scene)?;
        let selected = if snapshot.is_null() {
            None
        } else {
            let Some(identity) = snapshot.get("snapshot_id").and_then(Json::as_str) else {
                return Err(ExecError::package_runtime("Spatial snapshot reference is invalid"));
            };
            match self.snapshots.get(identity) {
                Some(selected) => Some(selected.clone()),
                None => {
                    return Err(ExecError::package_runtime("Spatial snapshot is unavailable in this action"))
                }
            }
        };
        let admitted_scene = match &selected {
            Some(selected) => selected.clone(),
            None => self.engines[scene_path].snapshot().clone(),
        };
        let mut request = JsonMap::new();
        request.insert("kind".into(), Json::from(operation));
        for (key, value) in query {
            request.insert(key.clone(), value.clone());
        }
        let canonical =
            admit_spatial_query(&admitted_scene, &Json::Object(request)).map_err(spatial_error)?.as_payload();
        // An explicit-origin circle overlap depends only on the two body heights and the fixed
        // position, so its result survives a move that keeps them.
        let mut identity = admitted_scene.snapshot_id.clone();
        let mut proof = canonical.clone();
        if operation == "circle_overlap" {
            for field in ["origin", "destination"] {
                if let Some(Json::Array(items)) = proof.get(field).cloned() {
                    proof.insert(field.into(), Json::Array(items.iter().map(proof_number).collect()));
                }
            }
            for field in ["moving_radius", "fixed_radius"] {
                let value = proof_number(&proof[field]);
                proof.insert(field.into(), value);
            }
            if selected.is_none() && canonical.contains_key("origin") {
                let payload = admitted_scene.payload();
                let body = |field: &str| {
                    payload["entities"]
                        .as_array()
                        .expect("admitted entities")
                        .iter()
                        .find(|row| row["id"] == canonical[field])
                        .expect("an admitted body")
                };
                let (moving, fixed) = (body("moving_entity_id"), body("fixed_entity_id"));
                let facts = Json::Array(vec![
                    proof_number(&moving["height"]),
                    proof_number(&fixed["height"]),
                    Json::Array(
                        fixed["position"].as_array().expect("a position").iter().map(proof_number).collect(),
                    ),
                ]);
                let digest: String = sylvia_base::hash::sha256(&canonical_json_bytes(&facts))
                    .iter()
                    .map(|b| format!("{b:02x}"))
                    .collect();
                identity = format!("circle_overlap:{digest}");
            }
        }
        let key =
            (scene_path.to_owned(), identity, selected.is_some(), canonical_json_bytes(&Json::Object(proof)));
        if let Some(cached) = self.query_results.get(&key) {
            return Ok(Json::Object(cached.clone()));
        }
        let engine = &mut self.engines[scene_path];
        let result = engine
            .query(&Json::Object(canonical.clone()), selected.as_ref())
            .map_err(spatial_error)?
            .as_payload();
        self.record_evidence(operation, &Json::Object(canonical), &result)?;
        self.query_results.insert(key, result.clone());
        Ok(Json::Object(result))
    }

    /// `spatial_mutation(scene_path, request)` past its input check: the advanced scene, which
    /// the bridge writes back to package state, and the mutation's evidence payload.
    pub fn mutation(
        &mut self,
        scene_path: &str,
        scene: &Json,
        request: &Json,
    ) -> Result<(JsonMap, Json), ExecError> {
        let engine = self.engine(scene_path, scene)?;
        let mutation = mutate_spatial_scene(engine.snapshot(), request).map_err(spatial_error)?;
        let advanced = mutation.scene.as_payload();
        engine.replace_scene(&Json::Object(advanced.clone())).map_err(spatial_error)?;
        let result = mutation.as_payload();
        self.record_evidence("scene_mutation", request, &result)?;
        Ok((advanced, Json::Object(result)))
    }

    /// `register_spatial_private_variable(name)`.
    pub fn register_private(&mut self, name: &str) {
        self.private.insert(name.to_owned());
    }

    /// `register_state_parameter(name, source)`.
    pub fn register_state_parameter(&mut self, name: &str, source: &str) {
        self.state_parameters.insert(name.to_owned(), source.to_owned());
    }

    /// `register_spatial_aggregate(name, source)`: every text in the source expression.
    pub fn register_aggregate(&mut self, name: &str, source: &Value) {
        fn visit(value: &Value, expressions: &mut Vec<String>) {
            match value {
                Value::Str(text) => {
                    if !expressions.iter().any(|known| **known == **text) {
                        expressions.push(text.to_string());
                    }
                }
                Value::Dict(map) => map.values().for_each(|child| visit(child, expressions)),
                Value::List(items) | Value::Tuple(items) => {
                    items.iter().for_each(|child| visit(child, expressions))
                }
                _ => {}
            }
        }
        visit(source, self.aggregates.entry(name.to_owned()).or_default());
    }

    /// `spatial_private_variables`: the spatial results, the state parameters read from an open
    /// scene, and every aggregate built from either, to a fixed point.
    pub fn private_variables(&self) -> HashSet<String> {
        let paths: Vec<&String> = self.engines.keys().collect();
        let mut private = self.private.clone();
        for (name, source) in &self.state_parameters {
            if paths.iter().any(|path| source == *path || source.starts_with(&format!("{path}."))) {
                private.insert(name.clone());
            }
        }
        let mut pending: Vec<(&String, &Vec<String>)> = self.aggregates.iter().collect();
        loop {
            let copied: Vec<String> = pending
                .iter()
                .filter(|(_, expressions)| {
                    expressions.iter().any(|expression| {
                        identifiers(expression).iter().any(|token| private.contains(*token))
                            || paths.iter().any(|path| names_path(expression, path))
                    })
                })
                .map(|(name, _)| (*name).clone())
                .collect();
            if copied.is_empty() {
                break;
            }
            pending.retain(|(name, _)| !copied.contains(name));
            private.extend(copied);
            if pending.is_empty() {
                break;
            }
        }
        private
    }

    /// `spatial_evidence`.
    pub fn evidence(&self) -> &[JsonMap] {
        &self.evidence
    }
}

/// `re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expression)`.
fn identifiers(expression: &str) -> Vec<&str> {
    let bytes = expression.as_bytes();
    let mut tokens = Vec::new();
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index].is_ascii_alphabetic() || bytes[index] == b'_' {
            let start = index;
            while index < bytes.len() && (bytes[index].is_ascii_alphanumeric() || bytes[index] == b'_') {
                index += 1;
            }
            tokens.push(&expression[start..index]);
        } else {
            index += 1;
        }
    }
    tokens
}

/// Python's `\w` on one character.
fn word(c: char) -> bool {
    c.is_alphanumeric() || c == '_'
}

/// `re.search(r"(?<![\w.])" + re.escape(path) + r"(?=$|[.\s\[])", expression)`.
fn names_path(expression: &str, path: &str) -> bool {
    let mut start = 0;
    while let Some(offset) = expression[start..].find(path) {
        let at = start + offset;
        let before = expression[..at].chars().next_back();
        let after = expression[at + path.len()..].chars().next();
        let clear_before = !before.is_some_and(|c| word(c) || c == '.');
        let clear_after = match after {
            None => true,
            Some(c) => c == '.' || c == '[' || c.is_whitespace(),
        };
        if clear_before && clear_after {
            return true;
        }
        start = at + expression[at..].chars().next().map_or(1, char::len_utf8);
    }
    false
}

/// `_mutable_json` of a resolved spatial input, as admission reads it.
pub fn input_json(value: &Value) -> Result<Json, ExecError> {
    super::bridge::json_safe(value)
}

/// A mutation's or query's JSON result as an execution value.
pub fn result_value(result: Json) -> Value {
    Value::from_json(&result)
}

/// The advanced scene as the value the bridge writes back.
pub fn scene_value_of(scene: JsonMap) -> Value {
    Value::from_json(&Json::Object(scene))
}
