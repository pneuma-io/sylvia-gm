//! Compare sylvia_domain::panel_layout with the frozen Python's `parse_panel_layout`.
//!
//! Regenerate with `tests/vectors/generate_panel_layout_vectors.py` (needs the snapshot's Python).

use serde_json::json;
use sylvia_base::pyjson;
use sylvia_base::Json;
use sylvia_domain::package_library::{canonical_json_bytes, SurfaceDeclaration};
use sylvia_domain::panel_layout::parse_panel_layout;
use sylvia_domain::DomainError;

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/panel_layout_vectors.json")).expect("vector file is valid JSON")
}

fn surfaces(rows: &Json) -> Vec<SurfaceDeclaration> {
    rows.as_array()
        .unwrap()
        .iter()
        .map(|row| {
            let mut surface = SurfaceDeclaration::plain(row[0].as_str().unwrap().into(), "cap".into());
            surface.renderer = row[1].as_str().map(str::to_owned);
            surface.allowed_placements =
                row[2].as_array().unwrap().iter().map(|p| p.as_str().unwrap().into()).collect();
            surface
        })
        .collect()
}

/// The generator's `apply(document, edits)`.
fn apply(base: &Json, edits: &[Json]) -> Json {
    let mut document = base.clone();
    for edit in edits {
        let (op, path, value) = (edit[0].as_str().unwrap(), edit[1].as_array().unwrap(), &edit[2]);
        let Some((last, parents)) = path.split_last() else {
            document = value.clone();
            continue;
        };
        let mut parent = &mut document;
        for part in parents {
            parent = match part {
                Json::String(key) => &mut parent[key.as_str()],
                index => &mut parent[index.as_u64().unwrap() as usize],
            };
        }
        match (op, last) {
            ("delete", Json::String(key)) => {
                parent.as_object_mut().unwrap().shift_remove(key.as_str());
            }
            ("delete", index) => {
                parent.as_array_mut().unwrap().remove(index.as_u64().unwrap() as usize);
            }
            (_, Json::String(key)) => parent[key.as_str()] = value.clone(),
            (_, index) => parent[index.as_u64().unwrap() as usize] = value.clone(),
        }
    }
    document
}

fn outcome(data: &[u8], surfaces: &[SurfaceDeclaration]) -> Json {
    match parse_panel_layout(data, surfaces) {
        Ok(parsed) => json!(["ok", String::from_utf8(canonical_json_bytes(&Json::Object(parsed))).unwrap()]),
        Err(DomainError::Diagnosed { error_type, message, diagnostics }) => {
            json!([
                error_type,
                message,
                diagnostics.iter().map(|d| Json::Object(d.as_payload())).collect::<Vec<_>>()
            ])
        }
        Err(error) => json!([error.python_class(), error.message()]),
    }
}

#[test]
fn panel_layouts_parse_like_python() {
    let vectors = vectors();
    let cases = vectors["cases"].as_array().unwrap();
    assert!(cases.len() > 300);
    let mut failures = Vec::new();
    for case in cases {
        let surfaces = surfaces(&vectors["surfaces"][case["surfaces"].as_str().unwrap()]);
        let data = match case["raw"].as_str() {
            Some(raw) => raw.as_bytes().to_vec(),
            None => canonical_json_bytes(&apply(&vectors["base"], case["edits"].as_array().unwrap())),
        };
        let actual = outcome(&data, &surfaces);
        if actual != case["outcome"] {
            failures.push(format!("{}: python {}, rust {actual}", case["edits"], case["outcome"]));
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {} differ: {:#?}",
        failures.len(),
        cases.len(),
        &failures[..failures.len().min(4)]
    );
}
