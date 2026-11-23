//! `player_ui_surfaces` against the real Python function
//! (`tests/vectors/generate_launch_compatibility_vectors.py`): the surfaces it returns, with
//! their key order, or the exception's class and text, for SRD Lite as the primary package, as a
//! dependency, another primary, an empty lock, and surfaces whose actions Python cannot filter.

use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{PackageLock, PackageLockEntry, PackageRole};

fn lock(spec: &Json) -> PackageLock {
    if spec.is_null() {
        return PackageLock::new(Vec::new()).unwrap();
    }
    let entry = |id: &str, sha: char, role: PackageRole, order: i64| {
        PackageLockEntry::new(id, "1.0.0", sha.to_string().repeat(64), role, order, Vec::new(), Vec::new())
            .unwrap()
    };
    let mut entries = vec![entry(spec["primary"].as_str().unwrap(), 'a', PackageRole::Primary, 0)];
    if let Some(dependency) = spec["dependency"].as_str() {
        entries.push(entry(dependency, 'b', PackageRole::Dependency, 1));
    }
    PackageLock::new(entries).unwrap()
}

#[test]
fn player_ui_surfaces_match_python() {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/vectors/launch_compatibility_vectors.json");
    let text = std::fs::read_to_string(&path)
        .unwrap_or_else(|error| panic!("cannot read {}: {error}", path.display()));
    let vectors: Json = serde_json::from_str(&text).unwrap();
    let cases = vectors["cases"].as_array().unwrap();
    assert_eq!(cases.len(), 11);
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let surfaces: Vec<JsonMap> = case["surfaces"]
            .as_array()
            .unwrap()
            .iter()
            .map(|surface| surface.as_object().unwrap().clone())
            .collect();
        let got = sylvia_app::launch_compatibility::player_ui_surfaces(&surfaces, &lock(&case["lock"]));
        match (&got, case.get("result"), case.get("error")) {
            (Ok(result), Some(want), None) => {
                let result = Json::Array(result.iter().cloned().map(Json::Object).collect());
                // Compared as text, so key order counts.
                assert_eq!(result.to_string(), want.to_string(), "{name}");
            }
            (Err(error), None, Some(want)) => {
                assert_eq!(error.python_class(), want["class"].as_str().unwrap(), "{name}");
                assert_eq!(error.message(), want["message"].as_str().unwrap(), "{name}");
            }
            _ => panic!("{name}: got {got:?}, want {case}"),
        }
    }
}
