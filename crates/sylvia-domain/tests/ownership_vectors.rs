//! Compare sylvia_domain::state_ownership with vectors from the frozen Python.
//!
//! Regenerate with `tests/vectors/generate_ownership_vectors.py` (needs the snapshot's Python).

use sylvia_base::pyjson;
use sylvia_domain::state_ownership::owner_matches_package_lock;

#[test]
fn ownership_matches_python() {
    let vectors = pyjson::loads(include_str!("vectors/ownership_vectors.json")).unwrap();
    let cases = vectors["cases"].as_array().unwrap();
    assert_eq!(cases.len(), 1500);
    for case in cases {
        let packages: Vec<&str> =
            case["packages"].as_array().unwrap().iter().map(|p| p.as_str().unwrap()).collect();
        let actual =
            owner_matches_package_lock(packages, case["namespace"].as_str().unwrap(), case["owner"].as_str());
        assert_eq!(actual, case["matches"].as_bool().unwrap(), "{case}");
    }
}
