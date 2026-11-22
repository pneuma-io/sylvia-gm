//! Compare sylvia_app::plan_spatial_admission and plan_network_admission with the frozen Python.
//!
//! Regenerate with `tests/vectors/generate_plan_admission_vectors.py` (needs the snapshot's Python).

use serde_json::json;
use sylvia_app::plan_network_admission::validate_plan_networks;
use sylvia_app::plan_spatial_admission::{plan_spatial_warnings, validate_plan_spatial_scenes};
use sylvia_base::{pyjson, Json};
use sylvia_domain::package_library::{manifest_from_record, SurfaceDeclaration};
use sylvia_domain::{DomainError, DomainResult};

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/plan_admission_vectors.json")).expect("vector file is valid JSON")
}

fn repository_json(path: &str) -> Json {
    let full = format!("{}/../../{path}", env!("CARGO_MANIFEST_DIR"));
    pyjson::loads(&std::fs::read_to_string(&full).unwrap_or_else(|error| panic!("{full}: {error}"))).unwrap()
}

/// The generator's `apply(document, edits)`.
fn apply(document: &mut Json, edits: &[Json]) {
    for edit in edits {
        let (op, path, value) = (edit[0].as_str().unwrap(), edit[1].as_array().unwrap(), edit[2].clone());
        let (last, parents) = path.split_last().unwrap();
        let parent = parents.iter().fold(&mut *document, |node, part| match part {
            Json::String(key) => &mut node[key.as_str()],
            index => &mut node[index.as_u64().unwrap() as usize],
        });
        match (op, last) {
            ("delete", Json::String(key)) => {
                parent.as_object_mut().unwrap().shift_remove(key.as_str());
            }
            ("delete", index) => {
                parent.as_array_mut().unwrap().remove(index.as_u64().unwrap() as usize);
            }
            (_, Json::String(key)) => {
                parent.as_object_mut().unwrap().insert(key.clone(), value);
            }
            (_, index) => parent[index.as_u64().unwrap() as usize] = value,
        }
    }
}

fn spec(surface: &SurfaceDeclaration) -> Json {
    json!({
        "identity": surface.identity,
        "capability": surface.capability,
        "renderer": surface.renderer,
        "source": surface.source.clone().map(Json::Object),
    })
}

fn surfaces(specs: &Json) -> Vec<SurfaceDeclaration> {
    specs
        .as_array()
        .unwrap()
        .iter()
        .map(|item| SurfaceDeclaration {
            renderer: item["renderer"].as_str().map(str::to_owned),
            source: item["source"].as_object().cloned(),
            ..SurfaceDeclaration::plain(
                item["identity"].as_str().unwrap().into(),
                item["capability"].as_str().unwrap().into(),
            )
        })
        .collect()
}

fn observed<T: Into<Json>>(result: DomainResult<T>) -> Json {
    match result {
        Ok(value) => match value.into() {
            Json::Null => json!(["ok"]),
            value => json!(["ok", value]),
        },
        Err(error @ DomainError::Diagnosed { .. }) | Err(error @ DomainError::Rejected { .. }) => json!([
            error.python_class(),
            error.message(),
            error.diagnostics().iter().map(|d| Json::Object(d.as_payload())).collect::<Vec<_>>()
        ]),
        Err(error) => json!([error.python_class(), error.message(), []]),
    }
}

#[test]
fn plan_spatial_and_network_admission_match_python() {
    let vectors = vectors();
    let srd = manifest_from_record(&repository_json(vectors["srd"].as_str().unwrap()), Some(4)).unwrap();
    let srd_specs: Vec<Json> = srd.ui_surfaces.iter().map(spec).collect();
    assert_eq!(
        Json::Array(srd_specs),
        vectors["surfaces"]["srd"],
        "the Rust manifest gives Python's SRD surfaces"
    );
    let cases = vectors["cases"].as_array().unwrap();
    assert!(cases.len() > 1000);
    let mut failures = Vec::new();
    for case in cases {
        let mut payload = match case["plan"].as_str() {
            Some(path) => repository_json(path),
            None => vectors["payloads"][case["payload"].as_str().unwrap()].clone(),
        };
        apply(&mut payload, vectors["setups"][case["setup"].as_str().unwrap()].as_array().unwrap());
        apply(&mut payload, case["edits"].as_array().unwrap());
        let before = payload.clone();
        let payload_map = payload.as_object().unwrap();
        let declared = surfaces(&vectors["surfaces"][case["surfaces"].as_str().unwrap()]);
        let actual = [
            ("spatial", observed(validate_plan_spatial_scenes(payload_map, &declared).map(|()| Json::Null))),
            ("network", observed(validate_plan_networks(payload_map, &declared).map(|()| Json::Null))),
            (
                "warnings",
                observed(
                    plan_spatial_warnings(payload_map, &declared)
                        .map(|warnings| Json::Array(warnings.into_iter().map(Json::Object).collect())),
                ),
            ),
        ];
        assert_eq!(payload, before);
        for (name, actual) in actual {
            if actual != case[name] {
                failures.push(format!(
                    "{} {} {} {name}: python {}, rust {actual}",
                    case["plan"], case["surfaces"], case["edits"], case[name]
                ));
            }
        }
    }
    let shown: Vec<String> = failures.iter().take(6).map(|f| f.chars().take(900).collect()).collect();
    assert!(failures.is_empty(), "{} of {} differ: {:#?}", failures.len(), cases.len() * 3, shown);
}
