//! Compare sylvia-app::tactical_grid::resolve_surface_source with the frozen Python.
//!
//! Regenerate with `tests/vectors/generate_surface_source_vectors.py` (needs the snapshot's Python).

use serde_json::{json, Value as Json};
use sylvia_app::tactical_grid::{resolve_surface_source, SurfaceParticipant};
use sylvia_domain::error::DomainError;

/// A participant with whichever attributes the case gives.
struct Fixture {
    kind: Option<String>,
    participant_id: Option<String>,
    data: Json,
}

impl SurfaceParticipant for Fixture {
    fn kind(&self) -> Option<&str> {
        self.kind.as_deref()
    }
    fn participant_id(&self) -> Option<&str> {
        self.participant_id.as_deref()
    }
    fn data(&self) -> Json {
        self.data.clone()
    }
}

fn fixture(entry: &Json) -> Fixture {
    Fixture {
        kind: entry.get("kind").and_then(Json::as_str).map(str::to_owned),
        participant_id: entry.get("participant_id").and_then(Json::as_str).map(str::to_owned),
        data: entry.get("data").cloned().unwrap_or(Json::Null),
    }
}

fn class(error: &DomainError) -> &'static str {
    match error {
        DomainError::Value(_) => "ValueError",
        DomainError::Type(_) => "TypeError",
        DomainError::Diagnosed { error_type, .. }
        | DomainError::Rejected { error_type, .. }
        | DomainError::Uncaught { error_type, .. } => error_type,
    }
}

#[test]
fn surface_sources_resolve_as_python() {
    let vectors: Json =
        serde_json::from_str(include_str!("vectors/surface_source_vectors.json")).expect("valid JSON");
    let state = vectors["state"].as_object().unwrap();
    let cases = vectors["cases"].as_array().unwrap();
    assert!(cases.len() > 1500);
    let mut failures = Vec::new();
    for (index, case) in cases.iter().enumerate() {
        let participants: Vec<Fixture> = case["participants"].as_array().unwrap().iter().map(fixture).collect();
        let actual = match resolve_surface_source(&case["source"], state, &participants) {
            Ok(value) => json!({"result": value}),
            Err(error) => json!({"error": [class(&error), error.to_string()]}),
        };
        let mut wanted = serde_json::Map::new();
        for key in ["result", "error"] {
            if let Some(value) = case.get(key) {
                wanted.insert(key.into(), value.clone());
            }
        }
        let (actual, wanted) = (serde_json::to_string(&actual).unwrap(), serde_json::to_string(&wanted).unwrap());
        if actual != wanted {
            failures.push(format!("case {index}: {}\n  python {wanted}\n  rust   {actual}", case["source"]));
        }
    }
    assert!(failures.is_empty(), "{} of {} differ, first: {:#?}", failures.len(), cases.len(), &failures[..failures.len().min(5)]);
}
